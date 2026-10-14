"""Public metadata for papers that are not in the Papers With Code catalog.

Chat agents ask about arXiv IDs, DOIs, and titles the catalog never imported.
A bare ``not_found`` sends them into identical retries; public arXiv, Hugging
Face Papers, and Crossref metadata lets them answer while saying clearly that
the paper is outside the catalog. Every lookup is best effort: a failing or
slow source yields ``None`` and the caller keeps its original error.
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from xml.etree import ElementTree

from pwc_mcp import __version__

logger = logging.getLogger(__name__)

ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})", re.IGNORECASE)
ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)
ARXIV_DOI = re.compile(r"10\.48550/arxiv\.(.+)", re.IGNORECASE)
DOI = re.compile(r"10\.\d{4,9}/\S+")
GITHUB_REPO = re.compile(r"https?://github\.com/[\w.-]+/[\w.-]+", re.IGNORECASE)
MAX_RESPONSE_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 6
ATOM = "{http://www.w3.org/2005/Atom}"
NOT_IN_CATALOG = "not in the Papers With Code catalog"


def normalize_title(value: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def short_name(title: object) -> str:
    """The name before a title's colon: "MuseTalk" for "MuseTalk: Real-Time ..."."""
    text = str(title or "")
    return normalize_title(text.split(":", 1)[0]) if ":" in text else ""


def title_matches(reference: str, title: object) -> bool:
    target = normalize_title(reference)
    return bool(target) and target in {normalize_title(title), short_name(title)}


def arxiv_reference(reference: str) -> str | None:
    """Return the arXiv ID named by an ID, arXiv/HF URL, or arXiv DOI."""
    candidate = reference.strip()
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme in {"http", "https"}:
        host = parsed.netloc.casefold().removeprefix("www.")
        parts = [part for part in parsed.path.split("/") if part]
        if host in {"arxiv.org", "export.arxiv.org"} and len(parts) >= 2:
            if parts[0] not in {"abs", "pdf", "html"}:
                return None
            candidate = "/".join(parts[1:]).removesuffix(".pdf")
        elif host == "huggingface.co" and len(parts) >= 2 and parts[0] == "papers":
            candidate = parts[1]
        elif host in {"doi.org", "dx.doi.org"}:
            candidate = urllib.parse.unquote(parsed.path.lstrip("/"))
        else:
            return None
    match = ARXIV_DOI.fullmatch(candidate)
    if match:
        candidate = match.group(1)
    candidate = ARXIV_VERSION.sub("", candidate.removeprefix("arXiv:"))
    return candidate if ARXIV_ID.fullmatch(candidate) else None


def doi_reference(reference: str) -> str | None:
    candidate = reference.strip()
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme in {"http", "https"}:
        if parsed.netloc.casefold().removeprefix("www.") not in {"doi.org", "dx.doi.org"}:
            return None
        candidate = urllib.parse.unquote(parsed.path.lstrip("/"))
    candidate = candidate.removeprefix("doi:").strip()
    return candidate if DOI.fullmatch(candidate) else None


class PublicPaperLookup:
    """Fetch public paper metadata from Hugging Face Papers, arXiv, and Crossref."""

    def _get(self, url: str) -> bytes | None:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, application/atom+xml, text/html;q=0.9",
                "User-Agent": f"pwc-mcp/{__version__} (+https://paperswithcode.co)",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read(MAX_RESPONSE_BYTES)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            # Never log the URL: it carries the user's reference.
            logger.info("pwc-mcp public paper lookup failed type=%s", type(error).__name__)
            return None

    def _json(self, url: str) -> Any:
        body = self._get(url)
        if body is None:
            return None
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def lookup(self, reference: str) -> dict[str, Any] | None:
        arxiv_id = arxiv_reference(reference)
        if arxiv_id:
            return self.by_arxiv_id(arxiv_id)
        doi = doi_reference(reference)
        if doi:
            return self.by_doi(doi)
        if urllib.parse.urlparse(reference.strip()).scheme in {"http", "https"}:
            return None
        return self.by_title(reference)

    def by_arxiv_id(self, arxiv_id: str) -> dict[str, Any] | None:
        quoted = urllib.parse.quote(arxiv_id, safe="./")
        paper = self._json(f"https://huggingface.co/api/papers/{quoted}")
        if isinstance(paper, dict) and paper.get("title"):
            return self._from_hf(paper)
        body = self._get(f"https://arxiv.org/abs/{quoted}")
        return self._from_arxiv_abs(arxiv_id, body) if body else None

    def by_title(self, title: str) -> dict[str, Any] | None:
        query = urllib.parse.urlencode({"q": title, "limit": 20})
        rows = self._json(f"https://huggingface.co/api/papers/search?{query}")
        papers = [
            row["paper"]
            for row in rows or []
            if isinstance(row, dict) and isinstance(row.get("paper"), dict)
        ]
        match = self._unique_title_match(title, papers)
        if match is not None:
            return self._from_hf(match)
        phrase = " ".join(title.replace('"', " ").split())
        query = urllib.parse.urlencode(
            {"search_query": f'ti:"{phrase}"', "max_results": 20}
        )
        body = self._get(f"https://export.arxiv.org/api/query?{query}")
        entries = self._arxiv_entries(body) if body else []
        match = self._unique_title_match(title, entries)
        return match

    def by_doi(self, doi: str) -> dict[str, Any] | None:
        payload = self._json(
            f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}"
        )
        work = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(work, dict) or not work.get("title"):
            return None
        titles = work.get("title")
        title = titles[0] if isinstance(titles, list) and titles else str(titles)
        parts = (work.get("issued") or {}).get("date-parts") or [[]]
        published = "-".join(f"{int(value):02d}" for value in parts[0]) or None
        abstract = re.sub(r"<[^>]+>", " ", str(work.get("abstract") or ""))
        abstract = " ".join(abstract.split()).removeprefix("Abstract ").strip()
        return {
            "id": doi,
            "arxiv_id": None,
            "doi": doi,
            "title": " ".join(str(title).split()),
            "authors": [
                " ".join(
                    filter(None, (author.get("given"), author.get("family")))
                )
                for author in work.get("author") or []
                if isinstance(author, dict)
            ],
            "abstract": abstract or None,
            "published": published,
            "url": str(work.get("URL") or f"https://doi.org/{doi}"),
            "pdf_url": None,
            "repositories": [],
            "project_pages": [],
            "metadata_source": "crossref",
        }

    @staticmethod
    def _unique_title_match(
        title: str, papers: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        exact = {
            str(paper.get("id")): paper
            for paper in papers
            if normalize_title(paper.get("title")) == normalize_title(title)
        }
        if not exact:
            exact = {
                str(paper.get("id")): paper
                for paper in papers
                if title_matches(title, paper.get("title"))
            }
        return next(iter(exact.values())) if len(exact) == 1 else None

    @staticmethod
    def _from_hf(paper: dict[str, Any]) -> dict[str, Any]:
        arxiv_id = str(paper.get("id") or "")
        repositories = [
            str(url)
            for url in (paper.get("githubRepo"),)
            if isinstance(url, str) and GITHUB_REPO.match(url)
        ]
        project_page = paper.get("projectPage")
        return {
            "id": arxiv_id,
            "arxiv_id": arxiv_id,
            "doi": None,
            "title": " ".join(str(paper.get("title") or "").split()),
            "authors": [
                str(author.get("name"))
                for author in paper.get("authors") or []
                if isinstance(author, dict) and author.get("name")
            ],
            "abstract": " ".join(str(paper.get("summary") or "").split()) or None,
            "published": str(paper.get("publishedAt") or "")[:10] or None,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
            "hf_url": f"https://huggingface.co/papers/{arxiv_id}",
            "repositories": repositories,
            "project_pages": [project_page] if isinstance(project_page, str) else [],
            "metadata_source": "huggingface",
        }

    @staticmethod
    def _from_arxiv_abs(arxiv_id: str, body: bytes) -> dict[str, Any] | None:
        text = body.decode("utf-8", errors="replace")
        meta: dict[str, list[str]] = {}
        for name, value in re.findall(
            r'<meta\s+name="citation_([a-z_]+)"\s+content="([^"]*)"', text
        ):
            meta.setdefault(name, []).append(html.unescape(value).strip())
        title = (meta.get("title") or [""])[0]
        if not title:
            return None
        published = (meta.get("date") or [""])[0].replace("/", "-") or None
        abstract = " ".join((meta.get("abstract") or [""])[0].split()) or None
        return {
            "id": arxiv_id,
            "arxiv_id": arxiv_id,
            "doi": (meta.get("doi") or [None])[0],
            "title": " ".join(title.split()),
            "authors": [_author_name(value) for value in meta.get("author", [])],
            "abstract": abstract,
            "published": published,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
            "repositories": _github_repos(abstract or ""),
            "project_pages": [],
            "metadata_source": "arxiv",
        }

    @staticmethod
    def _arxiv_entries(body: bytes) -> list[dict[str, Any]]:
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError:
            return []
        entries = []
        for entry in root.iter(f"{ATOM}entry"):
            identifier = (entry.findtext(f"{ATOM}id") or "").rsplit("/abs/", 1)[-1]
            arxiv_id = ARXIV_VERSION.sub("", identifier.strip())
            if not ARXIV_ID.fullmatch(arxiv_id):
                continue
            abstract = " ".join((entry.findtext(f"{ATOM}summary") or "").split())
            entries.append(
                {
                    "id": arxiv_id,
                    "arxiv_id": arxiv_id,
                    "doi": entry.findtext("{http://arxiv.org/schemas/atom}doi"),
                    "title": " ".join((entry.findtext(f"{ATOM}title") or "").split()),
                    "authors": [
                        " ".join((author.findtext(f"{ATOM}name") or "").split())
                        for author in entry.iter(f"{ATOM}author")
                    ],
                    "abstract": abstract or None,
                    "published": (entry.findtext(f"{ATOM}published") or "")[:10] or None,
                    "url": f"https://arxiv.org/abs/{arxiv_id}",
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                    "repositories": _github_repos(abstract),
                    "project_pages": [],
                    "metadata_source": "arxiv",
                }
            )
        return entries


def _github_repos(text: str) -> list[str]:
    """GitHub repository URLs in prose, without sentence punctuation or .git."""
    return sorted(
        {url.rstrip(".").removesuffix(".git") for url in GITHUB_REPO.findall(text)}
    )


def _author_name(value: str) -> str:
    """arXiv citation meta lists "Family, Given"; show "Given Family"."""
    family, _, given = value.partition(",")
    return f"{given.strip()} {family.strip()}".strip() if given else value.strip()
