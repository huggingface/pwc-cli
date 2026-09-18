from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlparse

from pwc_cli import queries
from pwc_cli.transport import Client, HTTPStatusError, Response, ResponseError

PAPER_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7}|\d+)", re.IGNORECASE)
ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)
CONTENT_VERSION = re.compile(r"[0-9a-f]{64}")
MARKDOWN_CHUNK_BYTES = 65_536
MARKDOWN_CACHE_ENTRIES = 256
MARKDOWN_CACHE_BYTES = 16 * 1024 * 1024
MARKDOWN_CACHE_SECONDS = 3600
# Search and paper listings change quickly; taxonomy is stable; the rest sits
# between (SPEC.md: one minute, ten minutes, and five minutes respectively).
VOLATILE_PATHS = frozenset(
    {"papers/", "papers/search", "papers/recent", "papers/trending"}
)
TAXONOMY_PREFIXES = (
    "tasks/",
    "methods/",
    "areas/",
    "conferences/",
    "organizations/",
    "frameworks/",
)


def cache_ttl(path: str) -> int:
    if path in VOLATILE_PATHS:
        return 60
    if path.startswith(TAXONOMY_PREFIXES):
        return 600
    return 300


@dataclass(frozen=True)
class PaperMarkdownChunk:
    paper: str
    source: str
    markdown: str
    content_version: str
    next_offset: int | None

    @property
    def truncated(self) -> bool:
        return self.next_offset is not None


class PaperVersionMismatchError(ResponseError):
    pass


class Transport(Protocol):
    def get(
        self, path: str, params: dict[str, object | None] | None = None
    ) -> Response: ...


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


class _TTLCache:
    def __init__(self, maximum: int = 256):
        self.maximum = maximum
        self._values: OrderedDict[object, tuple[float, object]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: object) -> object | None:
        now = time.monotonic()
        with self._lock:
            cached = self._values.get(key)
            if cached is None:
                return None
            expires_at, value = cached
            if expires_at <= now:
                del self._values[key]
                return None
            self._values.move_to_end(key)
            return value

    def put(self, key: object, value: object, ttl_seconds: int) -> None:
        with self._lock:
            self._values[key] = (time.monotonic() + ttl_seconds, value)
            self._values.move_to_end(key)
            while len(self._values) > self.maximum:
                self._values.popitem(last=False)


class _MarkdownChunkCache:
    def __init__(self):
        self._values: OrderedDict[object, tuple[float, int, PaperMarkdownChunk]] = (
            OrderedDict()
        )
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: object) -> PaperMarkdownChunk | None:
        now = time.monotonic()
        with self._lock:
            cached = self._values.get(key)
            if cached is None:
                return None
            expires_at, size, value = cached
            if expires_at <= now:
                self._bytes -= size
                del self._values[key]
                return None
            self._values.move_to_end(key)
            return value

    def put(self, key: object, value: PaperMarkdownChunk) -> None:
        size = len(value.markdown.encode("utf-8"))
        if size > MARKDOWN_CACHE_BYTES:
            return
        with self._lock:
            previous = self._values.pop(key, None)
            if previous is not None:
                self._bytes -= previous[1]
            self._values[key] = (
                time.monotonic() + MARKDOWN_CACHE_SECONDS,
                size,
                value,
            )
            self._bytes += size
            while (
                len(self._values) > MARKDOWN_CACHE_ENTRIES
                or self._bytes > MARKDOWN_CACHE_BYTES
            ):
                _, (_, removed_size, _) = self._values.popitem(last=False)
                self._bytes -= removed_size


class CatalogClient:
    """Typed, cached catalog operations shared by every MCP tool."""

    def __init__(self, transport: Transport | None = None):
        self.transport = transport or Client(timeout=25)
        self.cache = _TTLCache()
        self.markdown_cache = _MarkdownChunkCache()

    def _response(
        self,
        path: str,
        params: Mapping[str, object | None] | None = None,
        *,
        ttl: int,
    ) -> Response:
        key = ("response", path, _freeze(dict(params or {})))
        cached = self.cache.get(key)
        if isinstance(cached, Response):
            return cached
        response = self.transport.get(path, dict(params or {}))
        self.cache.put(key, response, ttl)
        return response

    def _json(
        self,
        path: str,
        params: dict[str, object | None] | None = None,
        *,
        ttl: int,
    ) -> dict[str, Any]:
        payload = self._response(path, params, ttl=ttl).json()
        if not isinstance(payload, dict):
            raise ResponseError("API returned an unexpected response shape")
        return payload

    def check_readiness(self) -> bool:
        response = self.transport.get("tasks/", {"page": 1, "page_size": 1})
        payload = response.json()
        if not isinstance(payload, dict):
            return False
        rows = payload.get("results")
        if rows is None:
            rows = payload.get("items")
        return isinstance(rows, list)

    def _text(self, path: str, *, ttl: int) -> str:
        key = ("text", path)
        cached = self.cache.get(key)
        if isinstance(cached, str):
            return cached
        response = self.transport.get(path)
        if response.headers.get("x-pwc-truncated") == "1":
            raise ResponseError(
                "Papers API returned incomplete Markdown; continuation is unavailable"
            )
        value = response.body.decode("utf-8", errors="replace")
        self.cache.put(key, value, ttl)
        return value

    @staticmethod
    def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        values = payload.get("results")
        if values is None:
            values = payload.get("items")
        # An empty list is a valid (empty) result, not a missing list.
        if not isinstance(values, list):
            raise ResponseError("API response did not contain a result list")
        return [item for item in values if isinstance(item, dict)]

    @staticmethod
    def _paper_from_url(reference: str) -> str | None:
        parsed = urlparse(reference)
        if parsed.scheme not in {"http", "https"}:
            return None
        parts = [part for part in parsed.path.split("/") if part]
        candidate = None
        if (
            parsed.netloc.casefold() in {"arxiv.org", "www.arxiv.org"}
            and len(parts) >= 2
        ):
            if parts[0] in {"abs", "pdf"}:
                candidate = "/".join(parts[1:])
        elif parsed.netloc.casefold() == "huggingface.co" and len(parts) >= 2:
            if parts[0] == "papers":
                candidate = parts[1]
        elif (
            parsed.netloc.casefold()
            in {
                "paperswithcode.co",
                "www.paperswithcode.co",
            }
            and len(parts) >= 2
            and parts[0] == "paper"
        ):
            candidate = parts[1]
        if candidate is None:
            return None
        candidate = candidate.removesuffix(".pdf")
        return ARXIV_VERSION.sub("", candidate)

    def _resolve_paper(self, reference: str) -> str:
        candidate = reference.strip()
        if not candidate:
            raise ResponseError("Paper reference cannot be empty")
        from_url = self._paper_from_url(candidate)
        slug_from_url = None
        if from_url:
            candidate = from_url
            parsed = urlparse(reference)
            if parsed.netloc.casefold() in {
                "paperswithcode.co",
                "www.paperswithcode.co",
            }:
                slug_from_url = candidate.casefold()
        elif urlparse(candidate).scheme in {"http", "https"}:
            # A DOI, publisher, or venue URL is never an exact title; searching
            # it would page through empty results before failing anyway.
            raise ResponseError(
                f"Paper URL not supported: {candidate}; only arXiv, Hugging Face, "
                "and Papers With Code URLs resolve"
            )
        candidate = ARXIV_VERSION.sub("", candidate)
        if PAPER_ID.fullmatch(candidate):
            if candidate.isdigit():
                return self._canonical_paper_id(candidate)
            return candidate
        query = candidate.replace("-", " ") if slug_from_url else candidate
        target = " ".join(candidate.split()).casefold()
        exact: dict[str, dict[str, Any]] = {}
        page = 1
        while page <= 10:
            payload = self._json(
                "papers/search",
                {"q": query, "page": page, "page_size": 100, "mode": "keyword"},
                ttl=60,
            )
            for item in self._rows(payload):
                paper = str(item.get("arxiv_id") or item.get("id") or "")
                title = " ".join(str(item.get("title") or "").split()).casefold()
                item_slug = str(item.get("slug") or "").casefold()
                if not item_slug:
                    item_slug = re.sub(r"[^a-z0-9]+", "-", title).strip("-")
                if paper and (
                    title == target or (slug_from_url and item_slug == slug_from_url)
                ):
                    exact.setdefault(paper, item)
            next_page = payload.get("next_page")
            if not isinstance(next_page, int) or next_page <= page:
                break
            page = next_page
        else:
            raise ResponseError("Too many results to resolve paper title safely")
        if len(exact) == 1:
            return next(iter(exact))
        if exact:
            choices = "; ".join(
                f"{item.get('title')} ({paper})" for paper, item in exact.items()
            )
            raise ResponseError(f"Paper title is ambiguous: {candidate}; {choices}")
        raise ResponseError(f"Paper title not found: {candidate}")

    def _canonical_paper_id(self, catalog_id: str) -> str:
        """Prefer the arXiv ID for a numeric catalog ID so every route accepts it.

        The Markdown read route stores arXiv papers under their arXiv ID and
        only external papers under the numeric ID; list tools hand out numeric
        IDs for both, so a bare numeric reference cannot tell them apart.
        """
        path = f"papers/{quote(catalog_id, safe='')}"
        record = self._json(path, ttl=cache_ttl(path))
        arxiv_id = record.get("arxiv_id")
        if isinstance(arxiv_id, str) and arxiv_id.strip():
            return ARXIV_VERSION.sub("", arxiv_id.strip())
        return catalog_id

    def _paper_exists(self, reference: str) -> bool:
        path = f"papers/{quote(reference, safe='.')}"
        try:
            self._json(path, ttl=cache_ttl(path))
        except HTTPStatusError as error:
            if error.status == 404:
                return False
            raise
        return True

    def resolve_paper(self, paper: str) -> str:
        return self._resolve_paper(paper)

    def read_paper_chunk(
        self,
        paper: str,
        *,
        offset: int = 0,
        content_version: str | None = None,
        limit: int = MARKDOWN_CHUNK_BYTES,
        resolved: bool = False,
    ) -> PaperMarkdownChunk:
        reference = paper if resolved else self._resolve_paper(paper)
        source = "external" if reference.isdigit() else "arxiv"
        cache_key = (reference, content_version, offset, limit)
        if content_version is not None:
            cached = self.markdown_cache.get(cache_key)
            if cached is not None:
                return cached
        try:
            response = self.transport.get(
                f"research/papers/{quote(reference, safe='.')}/read",
                {
                    "offset": offset,
                    "limit": limit,
                    "content_version": content_version,
                },
            )
        except HTTPStatusError as error:
            if error.status == 409:
                raise PaperVersionMismatchError(
                    "Paper Markdown changed; restart reading from the beginning"
                ) from error
            if error.status == 404 and not self._paper_exists(reference):
                raise ResponseError(f"Paper not found: {reference}") from error
            raise

        returned_version = response.headers.get("x-pwc-content-version", "")
        truncated = response.headers.get("x-pwc-truncated")
        next_text = response.headers.get("x-pwc-next-offset")
        try:
            markdown = response.body.decode("utf-8")
            next_offset = int(next_text) if next_text is not None else None
        except (UnicodeDecodeError, ValueError) as error:
            raise ResponseError(
                "Papers API returned an invalid Markdown chunk"
            ) from error
        if (
            CONTENT_VERSION.fullmatch(returned_version) is None
            or truncated not in {"0", "1"}
            or (content_version is not None and returned_version != content_version)
            or (truncated == "1" and next_offset is None)
            or (truncated == "0" and next_offset is not None)
            or (next_offset is not None and next_offset <= offset)
            or (next_offset is not None and next_offset - offset != len(response.body))
        ):
            detail = (
                "Papers API Markdown offset did not advance"
                if next_offset is not None and next_offset <= offset
                else "Papers API returned an invalid Markdown chunk"
            )
            raise ResponseError(detail)
        result = PaperMarkdownChunk(
            paper=reference,
            source=source,
            markdown=markdown,
            content_version=returned_version,
            next_offset=next_offset,
        )
        self.markdown_cache.put((reference, returned_version, offset, limit), result)
        return result

    def query(self, command: tuple[str, ...], options: Mapping[str, Any]) -> Any:
        """Run one read-only ``pwc`` command in-process against the cached catalog.

        Paper references are resolved first so URLs, legacy IDs, and exact
        titles behave exactly as they do for ``read_paper``; the CLI then
        receives the canonical identifier and applies its own validation,
        fail-closed filter checks, and JSON payload shape.
        """
        resolved = dict(options)
        if resolved.get("paper") is not None:
            resolved["paper"] = self._resolve_paper(str(resolved["paper"]))
        return queries.query(tuple(command), resolved, _CachedTransport(self))


class _CachedTransport:
    """``pwc_cli.transport.Client`` stand-in that serves CLI handlers from the cache."""

    def __init__(self, catalog: CatalogClient):
        self.catalog = catalog

    def get(
        self, path: str, params: Mapping[str, object | None] | None = None
    ) -> Response:
        return self.catalog._response(path, params, ttl=cache_ttl(path))
