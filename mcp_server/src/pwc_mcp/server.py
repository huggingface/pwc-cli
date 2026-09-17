from __future__ import annotations

import os
import time
from datetime import date
from typing import Annotated, Any, Literal, Protocol, TypeVar

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pwc_cli.transport import ResponseError, TransportError
from pydantic import BaseModel, Field

from pwc_mcp import __version__
from pwc_mcp.catalog import (
    AmbiguousError,
    CatalogError,
    PaperMarkdownChunk,
    PaperVersionMismatchError,
)
from pwc_mcp.cursors import (
    CURSOR_LIFETIME_SECONDS,
    MAX_CHUNK_BYTES,
    CursorCodec,
    CursorState,
)
from pwc_mcp.models import (
    AreaReference,
    BenchmarkPage,
    BenchmarkResult,
    EvaluationPage,
    MethodDetail,
    MethodResult,
    PaperInfoResult,
    PaperLineageResult,
    PaperPage,
    PaperReadResult,
    TaskDetail,
    TaskResult,
    TaxonomyPage,
    benchmark_summary,
    catalog_reference,
    merged_evaluations,
    metric_directions,
    paper_detail,
    paper_reference,
    paper_summary,
    taxonomy_reference,
)

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
Page = Annotated[int, Field(ge=1, le=100)]
Limit = Annotated[int, Field(ge=1, le=25)]
ResourceLimit = Annotated[int, Field(ge=1, le=10)]
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


def _catalog_error(error: Exception) -> ToolError:
    if isinstance(error, AmbiguousError):
        choices = ", ".join(
            " / ".join(value for value in candidate.values() if value)
            for candidate in error.candidates
        )
        return ToolError(f"ambiguous: {error}. Candidates: {choices}")
    if isinstance(error, CatalogError):
        return ToolError(f"{error.code}: {error}")
    if isinstance(error, TransportError):
        message = str(error).casefold()
        if "timed out" in message or "timeout" in message:
            return ToolError("upstream_timeout: the Papers With Code catalog timed out")
    return ToolError("upstream_error: the Papers With Code catalog request failed")


def _catalog_call(function: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except (ResponseError, TransportError) as error:
        raise _catalog_error(error) from error


Output = TypeVar("Output", bound=BaseModel)


def _tool_result(value: Output, markdown: str) -> CallToolResult:
    structured = value.model_dump(mode="json")
    return CallToolResult(
        content=[TextContent(type="text", text=markdown)],
        structured_content=structured,
    )


def _structured_model(result: Any, model: Any) -> Any:
    if isinstance(result, CallToolResult):
        return model.model_validate(result.structured_content)
    return result


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

    def resolve_paper(self, paper: str) -> str: ...

    def read_paper_chunk(
        self,
        paper: str,
        *,
        offset: int = 0,
        content_version: str | None = None,
        limit: int = MAX_CHUNK_BYTES,
        resolved: bool = False,
    ) -> PaperMarkdownChunk: ...

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

    def get_trending_papers(
        self, *, limit: int, max_age_days: int, min_velocity: float | None
    ) -> dict[str, Any]: ...

    def get_paper_evaluations(
        self, paper: str, *, page: int, limit: int
    ) -> dict[str, Any]: ...

    def get_paper_lineage(self, paper: str) -> dict[str, Any]: ...

    def get_task(self, task: str) -> dict[str, Any]: ...

    def list_tasks(
        self, *, search: str | None, page: int, limit: int
    ) -> dict[str, Any]: ...

    def get_method(self, method: str) -> dict[str, Any]: ...

    def list_methods(
        self, *, search: str | None, page: int, limit: int
    ) -> dict[str, Any]: ...

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
        self, benchmark: str, *, page: int, limit: int, is_open: bool | None
    ) -> dict[str, Any]: ...


def build_server(
    catalog: Catalog,
    *,
    read_chunk_bytes: int = MAX_CHUNK_BYTES,
    cursor_codec: CursorCodec | None = None,
) -> MCPServer:
    codec = cursor_codec or CursorCodec(
        os.environ.get("PWC_MCP_CURSOR_KEY_CURRENT", ""),
        previous_secret=os.environ.get("PWC_MCP_CURSOR_KEY_PREVIOUS") or None,
    )
    server = MCPServer(
        "pwc",
        title="Papers With Code",
        description="Read-only access to papers, tasks, methods, and benchmarks.",
        instructions=(
            "Use search_papers for relevance queries and list_papers for deterministic "
            "filters. Pass slugs or numeric IDs returned by list tools to exact task, "
            "method, and benchmark tools. Dates use YYYY-MM-DD. When a tool returns "
            "ambiguous, retry with a listed slug or ID. Structured data is in "
            "structuredContent; text is only a compact Markdown summary. Official "
            "implementation means catalog-designated official code, not an independent "
            "audit. Evaluation is_open is the catalog's implementation availability "
            "flag and may be null when unknown."
        ),
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
        """Search by relevance across paper title, topic, author, or arXiv ID. Use keyword for exact terms and semantic for concepts. Dates are YYYY-MM-DD. has_official_implementation means at least one repository is marked official in the catalog; it is not an independent code audit."""
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
        result = PaperPage(
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
        return _tool_result(
            result,
            f"Found {len(result.items)} papers."
            + (f" Next page: {result.next_page}." if result.next_page else ""),
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_paper_info(
        paper: Reference,
        include_resources: bool = False,
        repo_limit: ResourceLimit = 3,
    ) -> PaperInfoResult:
        """Get a paper by arXiv ID (for example 1706.03762), numeric PwC ID, supported URL, or exact title. By default returns official repositories only; include_resources adds other repositories, project pages, and Hugging Face models/datasets up to repo_limit repositories."""
        payload = _catalog_call(catalog.get_paper_info, paper, include_resources=True)
        result = PaperInfoResult(
            paper=paper_detail(
                payload,
                include_resources=include_resources,
                repo_limit=repo_limit,
            )
        )
        official = next(
            (repo.url for repo in result.paper.repositories if repo.is_official), None
        )
        markdown = f"## {result.paper.title}\n\n"
        if result.paper.url:
            markdown += f"[Paper]({result.paper.url})"
        if official:
            markdown += f" · [Official code]({official})"
        markdown += f" · {result.paper.code_repository_count} code repositories"
        return _tool_result(result, markdown)

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def read_paper(paper: Reference, cursor: str | None = None) -> PaperReadResult:
        """Read stored paper Markdown by arXiv/PwC ID, supported URL, or exact title. Pass next_cursor unchanged to continue a document; no_markdown means the catalog has no stored text."""
        reference = paper.strip()
        try:
            state = codec.decode(cursor, reference=reference) if cursor else None
        except ValueError as error:
            raise ToolError(str(error)) from error
        if state is None:
            canonical = _catalog_call(catalog.resolve_paper, reference)
            offset = 0
            content_version = None
            limit = read_chunk_bytes
            expires_at = int(time.time()) + CURSOR_LIFETIME_SECONDS
            source = "external" if canonical.isdigit() else "arxiv"
        else:
            canonical = state.paper
            offset = state.offset
            content_version = state.content_version
            limit = state.limit
            expires_at = state.expires_at
            source = state.source
        try:
            chunk = catalog.read_paper_chunk(
                canonical,
                offset=offset,
                content_version=content_version,
                limit=limit,
                resolved=True,
            )
        except PaperVersionMismatchError as error:
            raise ToolError(
                "paper changed; restart reading from the beginning"
            ) from error
        except (ResponseError, TransportError) as error:
            raise _catalog_error(error) from error
        if chunk.paper != canonical or chunk.source != source:
            raise ToolError("the Papers With Code catalog request failed")
        next_cursor = None
        if chunk.next_offset is not None:
            next_cursor = codec.encode(
                CursorState(
                    reference=reference,
                    paper=canonical,
                    source=source,
                    content_version=chunk.content_version,
                    offset=chunk.next_offset,
                    limit=limit,
                    expires_at=expires_at,
                )
            )
        result = PaperReadResult(
            paper=reference,
            markdown=chunk.markdown,
            truncated=chunk.truncated,
            next_cursor=next_cursor,
        )
        return _tool_result(result, chunk.markdown)

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
        """List papers in deterministic catalog order. Unlike search_papers this is for filters and pagination. Task/method filters should be slugs returned by list_tasks/list_methods; dates use YYYY-MM-DD."""
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
        result = PaperPage(
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
        return _tool_result(result, f"Listed {len(result.items)} papers.")

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_related_papers(paper: Reference, limit: Limit = 10) -> PaperPage:
        """Find papers related to one arXiv/PwC ID, supported URL, or exact title."""
        payload = _catalog_call(catalog.get_related_papers, paper, limit=limit)
        result = PaperPage(
            items=[
                paper_summary(item)
                for item in payload.get("results") or []
                if isinstance(item, dict)
            ],
            next_page=None,
        )
        return _tool_result(result, f"Found {len(result.items)} related papers.")

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_trending_papers(
        limit: Limit = 10,
        max_age_days: Annotated[int, Field(ge=1, le=3650)] = 30,
        min_velocity: Annotated[float, Field(ge=0)] | None = None,
    ) -> PaperPage:
        """List currently trending papers. max_age_days bounds paper age; min_velocity optionally filters the catalog's citation-velocity score."""
        payload = _catalog_call(
            catalog.get_trending_papers,
            limit=limit,
            max_age_days=max_age_days,
            min_velocity=min_velocity,
        )
        result = PaperPage(
            items=[
                paper_summary(item)
                for item in payload.get("results") or payload.get("items") or []
                if isinstance(item, dict)
            ],
            next_page=None,
        )
        return _tool_result(result, f"Found {len(result.items)} trending papers.")

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_paper_evaluations(
        paper: Reference, page: Page = 1, limit: Limit = 10
    ) -> EvaluationPage:
        """Get paginated benchmark evaluations reported for one paper, including protocol, source, update time, and task-scoped ranks. paper accepts an arXiv/PwC ID, supported URL, or exact title."""
        payload = _catalog_call(
            catalog.get_paper_evaluations, paper, page=page, limit=limit
        )
        rows = payload.get("results") or payload.get("items") or []
        result = EvaluationPage(
            paper=paper,
            evaluation_count=int(payload.get("count") or len(rows)),
            evaluations=merged_evaluations(
                [item for item in rows if isinstance(item, dict)]
            ),
            page=page,
            next_page=(
                int(payload["next_page"])
                if payload.get("next_page") is not None
                else None
            ),
        )
        return _tool_result(
            result,
            f"Found {result.evaluation_count} evaluation rows; returned {len(result.evaluations)} on page {page}."
            + (f" Next page: {result.next_page}." if result.next_page else ""),
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_paper_lineage(paper: Reference) -> PaperLineageResult:
        """Get explicit catalog predecessor and successor links for a paper. Empty lists mean no relationships are recorded, not proof that none exist."""
        payload = _catalog_call(catalog.get_paper_lineage, paper)
        current = payload.get("paper")
        if not isinstance(current, dict):
            raise TypeError("lineage response did not contain a paper")
        result = PaperLineageResult(
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
        return _tool_result(
            result,
            f"{result.paper.title}: {len(result.predecessors)} recorded predecessors, {len(result.successors)} recorded successors. Coverage is explicit catalog links only.",
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_task(task: Reference, benchmark_limit: ResourceLimit = 10) -> TaskResult:
        """Get an exact task by numeric ID or slug from list_tasks. Display names are accepted only when unambiguous. Benchmarks are capped by benchmark_limit."""
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
        benchmarks = sorted(
            [
                benchmark_summary(value)
                for value in payload.get("benchmarks") or []
                if isinstance(value, dict)
            ],
            key=lambda benchmark: (-benchmark.paper_count, benchmark.name.casefold()),
        )
        result = TaskResult(
            task=TaskDetail(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or "Unknown task"),
                slug=str(item.get("slug") or item.get("id") or ""),
                url=f"https://paperswithcode.co/tasks/{item.get('slug') or item.get('id')}",
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
                benchmark_count=len(benchmarks),
                benchmarks=benchmarks[:benchmark_limit],
            )
        )
        return _tool_result(
            result,
            f"## {result.task.name}\n\n{result.task.paper_count} papers · {result.task.benchmark_count} benchmarks; returned {len(result.task.benchmarks)}.",
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_tasks(
        search: str | None = None,
        page: Page = 1,
        limit: Limit = 10,
    ) -> TaxonomyPage:
        """List or search research tasks. Use this before get_task; pass the returned slug or numeric ID to avoid ambiguous display names."""
        payload = _catalog_call(
            catalog.list_tasks, search=search, page=page, limit=limit
        )
        result = TaxonomyPage(
            items=[
                taxonomy_reference(item, kind="tasks")
                for item in payload.get("results") or payload.get("items") or []
                if isinstance(item, dict)
            ],
            next_page=(
                int(payload["next_page"])
                if payload.get("next_page") is not None
                else None
            ),
        )
        return _tool_result(result, f"Listed {len(result.items)} tasks.")

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_method(method: Reference) -> MethodResult:
        """Get an exact method by numeric ID or slug from list_methods. Full/display names are accepted only when unambiguous."""
        item = _catalog_call(catalog.get_method, method)
        result = MethodResult(
            method=MethodDetail(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or "Unknown method"),
                slug=str(item.get("slug") or item.get("id") or ""),
                url=f"https://paperswithcode.co/methods/{item.get('slug') or item.get('id')}",
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
                source_url=(
                    f"https://paperswithcode.co{item['source_url']}"
                    if str(item.get("source_url") or "").startswith("/")
                    else str(item["source_url"])
                    if item.get("source_url")
                    else None
                ),
                source_title=(
                    str(item["source_title"]) if item.get("source_title") else None
                ),
                paper_count=int(item.get("paper_count") or 0),
            )
        )
        return _tool_result(
            result, f"## {result.method.name}\n\n{result.method.paper_count} papers."
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_methods(
        search: str | None = None,
        page: Page = 1,
        limit: Limit = 10,
    ) -> TaxonomyPage:
        """List or search methods. Use this before get_method; pass the returned slug or numeric ID to avoid ambiguous display names such as Mamba."""
        payload = _catalog_call(
            catalog.list_methods, search=search, page=page, limit=limit
        )
        result = TaxonomyPage(
            items=[
                taxonomy_reference(item, kind="methods")
                for item in payload.get("results") or payload.get("items") or []
                if isinstance(item, dict)
            ],
            next_page=(
                int(payload["next_page"])
                if payload.get("next_page") is not None
                else None
            ),
        )
        return _tool_result(result, f"Listed {len(result.items)} methods.")

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
        """List benchmark datasets, ordered by coverage, with optional task and availability filters. task should be a slug from list_tasks. Use search for a name query; minimum_evaluations filters coverage."""
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
        result = BenchmarkPage(
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
        return _tool_result(result, f"Listed {len(result.items)} benchmarks.")

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_benchmark(
        benchmark: Reference,
        page: Page = 1,
        limit: Limit = 10,
        is_open: bool | None = None,
    ) -> BenchmarkResult:
        """Get one exact benchmark and a page of evaluation rows. Rows expose split, shots/protocol, source, update time, metric direction, and task-specific rank scopes. Use next_page for continuation. is_open filters the catalog's open-implementation flag; null means unrecorded."""
        payload = _catalog_call(
            catalog.get_benchmark,
            benchmark,
            page=page,
            limit=limit,
            is_open=is_open,
        )
        item = payload.get("benchmark")
        if not isinstance(item, dict):
            raise TypeError("benchmark response did not contain a benchmark")
        evaluations = merged_evaluations(
            [value for value in payload.get("results") or [] if isinstance(value, dict)]
        )
        result = BenchmarkResult(
            benchmark=benchmark_summary(item),
            evaluation_count=int(payload.get("count") or 0),
            evaluations=evaluations,
            metric_directions=metric_directions(evaluations),
            page=page,
            next_page=(
                int(payload["next_page"])
                if payload.get("next_page") is not None
                else None
            ),
        )
        return _tool_result(
            result,
            f"## {result.benchmark.name}\n\n{result.evaluation_count} evaluation rows; returned {len(result.evaluations)} models on page {page}. Ranks are task-scoped."
            + (f" Next page: {result.next_page}." if result.next_page else ""),
        )

    @server.prompt(name="find_papers", title="Find papers")
    def find_papers_prompt(topic: str) -> str:
        """Find relevant papers and their official implementations for a topic."""
        return (
            f"Search Papers With Code for papers about {topic}. Start with "
            "search_papers, summarize the strongest matches, then call get_paper_info "
            "for official repositories. Cite the absolute paper and repository URLs."
        )

    @server.prompt(name="compare_leaderboard", title="Compare a leaderboard")
    def compare_leaderboard_prompt(benchmark: str) -> str:
        """Compare leading models on a named benchmark."""
        return (
            f"Use list_benchmarks to resolve {benchmark} without guessing, then call "
            "get_benchmark with its slug. Compare models only within that returned "
            "benchmark and explain metric direction when known."
        )

    @server.prompt(name="survey_task", title="Survey a research task")
    def survey_task_prompt(task: str) -> str:
        """Survey papers, methods, and benchmarks for a research task."""
        return (
            f"Resolve {task} with list_tasks, inspect it with get_task, then use its "
            "slug with list_papers and list_benchmarks. Summarize representative "
            "papers, official code, and high-coverage benchmarks."
        )

    @server.resource(
        "pwc://papers/{paper}",
        name="paper-info",
        title="Paper information",
        description="Canonical Papers With Code paper metadata.",
        mime_type="application/json",
    )
    def paper_info_resource(paper: str) -> str:
        result = _structured_model(get_paper_info(paper), PaperInfoResult)
        return result.model_dump_json()

    @server.resource(
        "pwc://papers/{paper}/markdown",
        name="paper-markdown",
        title="Paper Markdown",
        description="Complete stored Markdown for a paper when it fits one response.",
        mime_type="text/markdown",
    )
    def paper_markdown_resource(paper: str) -> str:
        result = _structured_model(read_paper(paper), PaperReadResult)
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
        result = _structured_model(get_task(task), TaskResult)
        return result.model_dump_json()

    @server.resource(
        "pwc://benchmarks/{benchmark}",
        name="benchmark",
        title="Benchmark leaderboard",
        description="Canonical benchmark metadata and leading evaluations.",
        mime_type="application/json",
    )
    def benchmark_resource(benchmark: str) -> str:
        result = _structured_model(get_benchmark(benchmark), BenchmarkResult)
        return result.model_dump_json()

    return server
