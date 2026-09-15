from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from typing import Any, Protocol
from urllib.parse import quote, urlparse

from pwc_cli.transport import Client, Response, ResponseError

PAPER_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7}|\d+)", re.IGNORECASE)
ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)


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


class CatalogClient:
    """Typed, cached catalog operations shared by every MCP tool."""

    def __init__(self, transport: Transport | None = None):
        self.transport = transport or Client(timeout=25)
        self.cache = _TTLCache()

    def _json(
        self,
        path: str,
        params: dict[str, object | None] | None = None,
        *,
        ttl: int,
    ) -> dict[str, Any]:
        key = ("json", path, _freeze(params or {}))
        cached = self.cache.get(key)
        if isinstance(cached, dict):
            return cached
        payload = self.transport.get(path, params).json()
        if not isinstance(payload, dict):
            raise ResponseError("API returned an unexpected response shape")
        self.cache.put(key, payload, ttl)
        return payload

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
        values = payload.get("results") or payload.get("items")
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
        candidate = ARXIV_VERSION.sub("", candidate)
        if PAPER_ID.fullmatch(candidate):
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

    @staticmethod
    def _exact(
        reference: str,
        items: list[dict[str, Any]],
        label: str,
        fields: tuple[str, ...] = ("name", "slug", "id"),
    ) -> dict[str, Any]:
        target = reference.strip().casefold()
        for field in fields:
            for item in items:
                if str(item.get(field) or "").strip().casefold() == target:
                    return item
        raise ResponseError(f"{label} not found: {reference}")

    def search_papers(
        self,
        *,
        query: str,
        mode: str = "keyword",
        page: int = 1,
        limit: int = 10,
        published_after: str | None = None,
        published_before: str | None = None,
        has_official_implementation: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, object | None] = {
            "q": query,
            "mode": mode,
            "page": page,
            "page_size": limit,
            "start_date": published_after,
            "end_date": published_before,
            "has_official_implementation": (
                True if has_official_implementation else None
            ),
        }
        payload = self._json("papers/search", params, ttl=60)
        if has_official_implementation and (payload.get("applied_filters") or {}).get(
            "has_official_implementation"
        ) not in {True, "true"}:
            raise ResponseError(
                "Papers API did not confirm has_official_implementation"
            )
        return payload

    def get_paper_info(self, paper: str, *, include_resources: bool) -> dict[str, Any]:
        reference = self._resolve_paper(paper)
        return self._json(
            f"papers/{quote(reference, safe='.')}",
            {"include_resources": include_resources},
            ttl=300,
        )

    def read_paper(self, paper: str) -> str:
        reference = self._resolve_paper(paper)
        return self._text(
            f"research/papers/{quote(reference, safe='.')}/read", ttl=3600
        )

    def list_papers(
        self,
        *,
        search: str | None = None,
        task: str | None = None,
        method: str | None = None,
        conference: str | None = None,
        framework: str | None = None,
        organization: str | None = None,
        authors: list[str] | None = None,
        published_after: str | None = None,
        published_before: str | None = None,
        order_by: str = "date_published",
        order_direction: str = "desc",
        page: int = 1,
        limit: int = 10,
    ) -> dict[str, Any]:
        requested = {
            key: value
            for key, value in {
                "task": task,
                "method": method,
                "conference": conference,
                "framework": framework,
                "organization": organization,
                "start_date": published_after,
                "end_date": published_before,
            }.items()
            if value is not None
        }
        params: dict[str, object | None] = {
            "search": search,
            **requested,
            "author": authors or None,
            "latest_only": True,
            "order_by": order_by,
            "order_dir": order_direction,
            "page": page,
            "page_size": limit,
        }
        payload = self._json("papers/", params, ttl=60)
        applied = payload.get("applied_filters")
        if requested and (
            not isinstance(applied, dict)
            or any(
                str(applied.get(key, "")).casefold() != str(value).casefold()
                for key, value in requested.items()
            )
        ):
            raise ResponseError("Papers API did not confirm requested catalog filters")
        return payload

    def get_related_papers(self, paper: str, *, limit: int) -> dict[str, Any]:
        reference = self._resolve_paper(paper)
        return self._json(
            f"papers/{quote(reference, safe='.')}/related",
            {"limit": limit},
            ttl=300,
        )

    def get_paper_lineage(self, paper: str) -> dict[str, Any]:
        reference = self._resolve_paper(paper)
        return self._json(
            f"research/papers/{quote(reference, safe='.')}/lineage", ttl=300
        )

    def get_task(self, task: str) -> dict[str, Any]:
        if task.strip().isdigit():
            task_id = task.strip()
        else:
            candidates = self._rows(
                self._json(
                    "tasks/",
                    {"q": task, "page": 1, "page_size": 100},
                    ttl=600,
                )
            )
            task_id = str(self._exact(task, candidates, "Task").get("id"))
        return self._json(f"tasks/{quote(task_id, safe='')}/page", ttl=600)

    def get_method(self, method: str) -> dict[str, Any]:
        if method.strip().isdigit():
            method_id = method.strip()
        else:
            candidates = self._rows(
                self._json(
                    "methods/",
                    {"q": method, "page": 1, "page_size": 100},
                    ttl=600,
                )
            )
            matched = self._exact(
                method,
                candidates,
                "Method",
                fields=("name", "full_name", "slug", "id"),
            )
            method_id = str(matched.get("id") or matched.get("slug"))
        return self._json(f"methods/{quote(method_id, safe='')}", ttl=600)

    def list_benchmarks(
        self,
        *,
        search: str | None = None,
        task: str | None = None,
        include_descendants: bool = False,
        minimum_evaluations: int | None = None,
        is_open: bool | None = None,
        page: int = 1,
        limit: int = 10,
    ) -> dict[str, Any]:
        return self._json(
            "datasets/",
            {
                "q": search,
                "task": task,
                "include_descendants": include_descendants,
                "min_eval_count": minimum_evaluations,
                "is_open": is_open,
                "ordering": "-paper_count",
                "page": page,
                "page_size": limit,
            },
            ttl=300,
        )

    def get_benchmark(
        self, benchmark: str, *, limit: int, is_open: bool | None
    ) -> dict[str, Any]:
        candidates = self._rows(
            self._json(
                "datasets/",
                {"q": benchmark, "page": 1, "page_size": 100},
                ttl=300,
            )
        )
        matched = self._exact(
            benchmark,
            candidates,
            "Benchmark",
            fields=("name", "full_name", "slug", "id"),
        )
        benchmark_id = str(matched.get("id"))
        evaluations = self._json(
            f"datasets/{quote(benchmark_id, safe='')}/evaluations/",
            {
                "page": 1,
                "page_size": limit,
                "ordering": "best_rank",
                "is_open": is_open,
            },
            ttl=300,
        )
        return {
            "benchmark": matched,
            "count": evaluations.get("count") or 0,
            "results": evaluations.get("results") or [],
        }
