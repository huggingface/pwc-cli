from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, Protocol

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pwc_cli.transport import ResponseError, TransportError
from pydantic import Field

from pwc_mcp import __version__
from pwc_mcp.cursors import decode_cursor, encode_cursor
from pwc_mcp.models import (
    AreaReference,
    BenchmarkPage,
    BenchmarkResult,
    MethodDetail,
    MethodResult,
    PaperInfoResult,
    PaperLineageResult,
    PaperPage,
    PaperReadResult,
    TaskDetail,
    TaskResult,
    benchmark_summary,
    catalog_reference,
    evaluation,
    paper_detail,
    paper_reference,
    paper_summary,
)

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
Page = Annotated[int, Field(ge=1, le=100)]
Limit = Annotated[int, Field(ge=1, le=25)]
Reference = Annotated[str, Field(min_length=1, max_length=500)]
Query = Annotated[str, Field(min_length=1, max_length=500)]
AuthorList = Annotated[list[str], Field(max_length=10)]


def _validate_date_range(start: str | None, end: str | None) -> None:
    try:
        start_date = date.fromisoformat(start) if start else None
        end_date = date.fromisoformat(end) if end else None
    except ValueError as error:
        raise ToolError("publication dates must use YYYY-MM-DD") from error
    if start_date and end_date and start_date > end_date:
        raise ToolError("published_after must be on or before published_before")


def _catalog_call(function: Any, *args: Any, **kwargs: Any) -> Any:
    """Turn upstream failures into deliberately generic, non-content-bearing errors."""
    try:
        return function(*args, **kwargs)
    except (ResponseError, TransportError) as error:
        raise ToolError("the Papers With Code catalog request failed") from error


class Catalog(Protocol):
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
    ) -> dict[str, Any]: ...

    def get_paper_info(
        self, paper: str, *, include_resources: bool
    ) -> dict[str, Any]: ...

    def read_paper(self, paper: str) -> str: ...

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
    ) -> dict[str, Any]: ...

    def get_related_papers(self, paper: str, *, limit: int) -> dict[str, Any]: ...

    def get_paper_lineage(self, paper: str) -> dict[str, Any]: ...

    def get_task(self, task: str) -> dict[str, Any]: ...

    def get_method(self, method: str) -> dict[str, Any]: ...

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
    ) -> dict[str, Any]: ...

    def get_benchmark(
        self, benchmark: str, *, limit: int, is_open: bool | None
    ) -> dict[str, Any]: ...


def build_server(catalog: Catalog, *, read_chunk_chars: int = 200_000) -> MCPServer:
    server = MCPServer(
        "pwc",
        title="Papers With Code",
        description="Read-only access to papers, tasks, methods, and benchmarks.",
        version=__version__,
        website_url="https://paperswithcode.co",
        cache_hints={
            "server/discover": CacheHint(ttl_ms=3_600_000, scope="public"),
            "tools/list": CacheHint(ttl_ms=3_600_000, scope="public"),
            "resources/templates/list": CacheHint(ttl_ms=3_600_000, scope="public"),
            "resources/read": CacheHint(ttl_ms=300_000, scope="public"),
        },
    )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def search_papers(
        query: Query,
        mode: Literal["keyword", "semantic"] = "keyword",
        page: Page = 1,
        limit: Limit = 10,
        published_after: str | None = None,
        published_before: str | None = None,
        has_official_implementation: bool = False,
    ) -> PaperPage:
        """Search papers by title, topic, author, or arXiv ID."""
        _validate_date_range(published_after, published_before)
        payload = _catalog_call(
            catalog.search_papers,
            query=query,
            mode=mode,
            page=page,
            limit=limit,
            published_after=published_after,
            published_before=published_before,
            has_official_implementation=has_official_implementation,
        )
        return PaperPage(
            items=[
                paper_summary(item)
                for item in payload.get("results") or []
                if isinstance(item, dict)
            ],
            next_page=(
                int(payload["next_page"])
                if payload.get("next_page") is not None
                else None
            ),
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_paper_info(paper: Reference) -> PaperInfoResult:
        """Get metadata for an arXiv ID, PwC ID, URL, or exact paper title."""
        payload = _catalog_call(catalog.get_paper_info, paper, include_resources=True)
        return PaperInfoResult(paper=paper_detail(payload))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def read_paper(paper: Reference, cursor: str | None = None) -> PaperReadResult:
        """Read stored paper Markdown, continuing oversized documents with a cursor."""
        markdown = _catalog_call(catalog.read_paper, paper)
        try:
            offset = decode_cursor(cursor, paper) if cursor else 0
        except ValueError as error:
            raise ToolError("invalid continuation cursor") from error
        if offset > len(markdown):
            raise ToolError("continuation cursor is beyond the paper content")
        end = min(offset + read_chunk_chars, len(markdown))
        truncated = end < len(markdown)
        return PaperReadResult(
            paper=paper,
            markdown=markdown[offset:end],
            truncated=truncated,
            next_cursor=encode_cursor(paper, end) if truncated else None,
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_papers(
        search: str | None = None,
        task: str | None = None,
        method: str | None = None,
        conference: str | None = None,
        framework: str | None = None,
        organization: str | None = None,
        authors: AuthorList | None = None,
        published_after: str | None = None,
        published_before: str | None = None,
        order_by: Literal[
            "date_published", "citation_count", "title"
        ] = "date_published",
        order_direction: Literal["asc", "desc"] = "desc",
        page: Page = 1,
        limit: Limit = 10,
    ) -> PaperPage:
        """List and filter papers in a deterministic catalog order."""
        _validate_date_range(published_after, published_before)
        payload = _catalog_call(
            catalog.list_papers,
            search=search,
            task=task,
            method=method,
            conference=conference,
            framework=framework,
            organization=organization,
            authors=authors or [],
            published_after=published_after,
            published_before=published_before,
            order_by=order_by,
            order_direction=order_direction,
            page=page,
            limit=limit,
        )
        return PaperPage(
            items=[
                paper_summary(item)
                for item in payload.get("results") or []
                if isinstance(item, dict)
            ],
            next_page=(
                int(payload["next_page"])
                if payload.get("next_page") is not None
                else None
            ),
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_related_papers(paper: Reference, limit: Limit = 10) -> PaperPage:
        """Find catalog papers related to one paper."""
        payload = _catalog_call(catalog.get_related_papers, paper, limit=limit)
        return PaperPage(
            items=[
                paper_summary(item)
                for item in payload.get("results") or []
                if isinstance(item, dict)
            ],
            next_page=None,
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_paper_lineage(paper: Reference) -> PaperLineageResult:
        """Get explicit predecessor and successor relationships for a paper."""
        payload = _catalog_call(catalog.get_paper_lineage, paper)
        current = payload.get("paper")
        if not isinstance(current, dict):
            raise TypeError("lineage response did not contain a paper")
        return PaperLineageResult(
            paper=paper_reference(current),
            predecessors=[
                paper_reference(item)
                for item in payload.get("predecessors") or []
                if isinstance(item, dict)
            ],
            successors=[
                paper_reference(item)
                for item in payload.get("successors") or []
                if isinstance(item, dict)
            ],
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_task(task: Reference) -> TaskResult:
        """Get an exact task by ID, slug, or name, including its benchmarks."""
        payload = _catalog_call(catalog.get_task, task)
        item = payload.get("task")
        if not isinstance(item, dict):
            raise TypeError("task response did not contain a task")
        area_item = payload.get("area")
        area = (
            AreaReference(
                id=str(area_item.get("id") or ""),
                name=str(area_item.get("name") or "Unknown area"),
            )
            if isinstance(area_item, dict)
            else None
        )
        return TaskResult(
            task=TaskDetail(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or "Unknown task"),
                slug=str(item.get("slug") or item.get("id") or ""),
                description=(
                    str(item["description"]) if item.get("description") else None
                ),
                paper_count=int(item.get("paper_count") or 0),
                area=area,
                parents=[
                    catalog_reference(value)
                    for value in payload.get("parents") or []
                    if isinstance(value, dict)
                ],
                children=[
                    catalog_reference(value)
                    for value in payload.get("children") or []
                    if isinstance(value, dict)
                ],
                benchmarks=[
                    benchmark_summary(value)
                    for value in payload.get("benchmarks") or []
                    if isinstance(value, dict)
                ],
            )
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_method(method: Reference) -> MethodResult:
        """Get an exact method by ID, slug, full name, or name."""
        item = _catalog_call(catalog.get_method, method)
        return MethodResult(
            method=MethodDetail(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or "Unknown method"),
                slug=str(item.get("slug") or item.get("id") or ""),
                full_name=str(item["full_name"]) if item.get("full_name") else None,
                description=(
                    str(item["description"]) if item.get("description") else None
                ),
                introduced_year=(
                    int(item["introduced_year"])
                    if item.get("introduced_year") is not None
                    else None
                ),
                source_paper_id=(
                    str(item["source_paper_id"])
                    if item.get("source_paper_id")
                    else None
                ),
                source_url=str(item["source_url"]) if item.get("source_url") else None,
                source_title=(
                    str(item["source_title"]) if item.get("source_title") else None
                ),
                paper_count=int(item.get("paper_count") or 0),
            )
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_benchmarks(
        search: str | None = None,
        task: str | None = None,
        include_descendants: bool = False,
        minimum_evaluations: int | None = None,
        is_open: bool | None = None,
        page: Page = 1,
        limit: Limit = 10,
    ) -> BenchmarkPage:
        """List benchmark datasets with optional task and availability filters."""
        payload = _catalog_call(
            catalog.list_benchmarks,
            search=search,
            task=task,
            include_descendants=include_descendants,
            minimum_evaluations=minimum_evaluations,
            is_open=is_open,
            page=page,
            limit=limit,
        )
        return BenchmarkPage(
            items=[
                benchmark_summary(item)
                for item in payload.get("results") or []
                if isinstance(item, dict)
            ],
            next_page=(
                int(payload["next_page"])
                if payload.get("next_page") is not None
                else None
            ),
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_benchmark(
        benchmark: Reference,
        limit: Limit = 10,
        is_open: bool | None = None,
    ) -> BenchmarkResult:
        """Get an exact benchmark and its top evaluation rows."""
        payload = _catalog_call(
            catalog.get_benchmark, benchmark, limit=limit, is_open=is_open
        )
        item = payload.get("benchmark")
        if not isinstance(item, dict):
            raise TypeError("benchmark response did not contain a benchmark")
        return BenchmarkResult(
            benchmark=benchmark_summary(item),
            evaluation_count=int(payload.get("count") or 0),
            evaluations=[
                evaluation(value)
                for value in payload.get("results") or []
                if isinstance(value, dict)
            ],
        )

    @server.resource(
        "pwc://papers/{paper}",
        name="paper-info",
        title="Paper information",
        description="Canonical Papers With Code paper metadata.",
        mime_type="application/json",
    )
    def paper_info_resource(paper: str) -> str:
        return get_paper_info(paper).model_dump_json()

    @server.resource(
        "pwc://papers/{paper}/markdown",
        name="paper-markdown",
        title="Paper Markdown",
        description="Complete stored Markdown for a paper when it fits one response.",
        mime_type="text/markdown",
    )
    def paper_markdown_resource(paper: str) -> str:
        result = read_paper(paper)
        if result.truncated:
            raise ValueError(
                "paper is too large for one resource response; use read_paper with its continuation cursor"
            )
        return result.markdown

    @server.resource(
        "pwc://tasks/{task}",
        name="task",
        title="Research task",
        description="Canonical Papers With Code task metadata and benchmarks.",
        mime_type="application/json",
    )
    def task_resource(task: str) -> str:
        return get_task(task).model_dump_json()

    @server.resource(
        "pwc://benchmarks/{benchmark}",
        name="benchmark",
        title="Benchmark leaderboard",
        description="Canonical benchmark metadata and leading evaluations.",
        mime_type="application/json",
    )
    def benchmark_resource(benchmark: str) -> str:
        return get_benchmark(benchmark).model_dump_json()

    return server
