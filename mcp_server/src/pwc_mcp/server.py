"""MCP tools that mirror every read-only ``pwc`` research command.

Each tool maps one CLI command and exposes each of its research flags as a
typed parameter, then runs the CLI handler in-process through the catalog.
``TOOL_COMMANDS``, ``PARAMETER_NAMES``, ``ENTITY_PARAMETERS`` and
``MCP_ONLY_PARAMETERS`` are the parity contract that ``tests/test_parity.py``
checks against the CLI parser.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from datetime import date
from functools import cache
from typing import Annotated, Any, Literal, Protocol, TypeVar

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pwc_cli import queries
from pwc_cli.cli import UsageError
from pwc_cli.transport import HTTPStatusError, ResponseError, TransportError
from pydantic import BaseModel, Field

from pwc_mcp import __version__
from pwc_mcp.catalog import PaperMarkdownChunk, PaperVersionMismatchError
from pwc_mcp.cursors import (
    CURSOR_LIFETIME_SECONDS,
    MAX_CHUNK_BYTES,
    CursorCodec,
    CursorReferenceMismatch,
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
    QueryResult,
    TaskDetail,
    TaskResult,
    benchmark_summary,
    catalog_reference,
    evaluation,
    metric_directions,
    paper_detail,
    paper_evaluation,
    paper_reference,
    paper_summary,
)

logger = logging.getLogger(__name__)
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
# Hosted ceiling on rows per response (SPEC.md); the CLI allows up to 100.
MAX_ROWS = 25
# Matches `pwc search --mode`; app.py counts omitted modes with this value.
DEFAULT_SEARCH_MODE = "hybrid"
# CLI lookup failures the caller can act on (an unknown or ambiguous name, an
# unconfirmed filter). Transport, HTTP, and response-shape failures stay generic.
CLIENT_FACING_ERRORS = (
    "Task not found",
    "Method not found",
    "Benchmark not found",
    "Conference not found",
    "Organization not found",
    "Framework not found",
    "Area not found",
    "Paper title not found",
    "Paper title is ambiguous",
    "Paper not found",
    "Paper URL not supported",
    "Paper reference cannot be empty",
    "Too many results to resolve paper title",
    "Papers API did not confirm",
)
GENERIC_ERROR = "the Papers With Code catalog request failed"
# Upstream validation failures the caller can correct; the detail is the
# public API's own message, bounded and stripped of control characters.
INVALID_ARGUMENT_STATUSES = frozenset({400, 422})
MAX_UPSTREAM_DETAIL_CHARS = 200


def _upstream_detail(error: HTTPStatusError) -> str:
    detail = "".join(
        ch if ch.isprintable() else " " for ch in str(error.detail or "")
    ).strip()
    return detail[:MAX_UPSTREAM_DETAIL_CHARS] or "the catalog rejected the request"


def catalog_error_message(error: Exception) -> str:
    """Return the CLI's own lookup message when it is actionable, else a generic one."""
    if isinstance(error, HTTPStatusError):
        if error.status == 404:
            return "not_found: the requested catalog record does not exist"
        if error.status in INVALID_ARGUMENT_STATUSES:
            return f"invalid_argument: {_upstream_detail(error)}"
        if error.status == 429:
            return "rate_limited: the Papers With Code catalog is rate limiting; retry later"
    if isinstance(error, TransportError):
        message = str(error).casefold()
        if "timeout" in message or "timed out" in message:
            return "upstream_timeout: the Papers With Code catalog timed out"
    if isinstance(error, ResponseError) and not isinstance(error, TransportError):
        message = str(error)
        if message.startswith(CLIENT_FACING_ERRORS):
            code = "ambiguous" if "ambiguous" in message.casefold() else "not_found"
            return f"{code}: {message}"
    # Only the exception class is logged: never the reference, query, or body.
    logger.warning("pwc-mcp generic catalog error type=%s", type(error).__name__)
    return f"upstream_error: {GENERIC_ERROR}"


# One read-only CLI command per tool.
TOOL_COMMANDS: dict[str, tuple[str, ...]] = {
    "search_papers": ("search",),
    "get_paper_info": ("paper", "info"),
    "get_paper_evaluations": ("paper", "evaluations"),
    "read_paper": ("paper", "read"),
    "list_papers": ("paper", "list"),
    "list_recent_papers": ("paper", "recent"),
    "list_trending_papers": ("paper", "trending"),
    "get_related_papers": ("paper", "related"),
    "get_paper_lineage": ("paper", "lineage", "list"),
    "get_task": ("task",),
    "list_tasks": ("task", "list"),
    "get_method": ("method",),
    "list_methods": ("method", "list"),
    "get_conference": ("conference",),
    "list_conferences": ("conference", "list"),
    "get_organization": ("organization",),
    "list_organizations": ("organization", "list"),
    "get_framework": ("framework",),
    "list_frameworks": ("framework", "list"),
    "get_benchmark": ("benchmark",),
    "list_benchmarks": ("benchmark", "list"),
}
# CLI destinations that keep their established MCP parameter name.
PARAMETER_NAMES: dict[str, str] = {
    "start_date": "published_after",
    "end_date": "published_before",
    "page_size": "limit",
    "author": "authors",
    "order_dir": "order_direction",
    "min_eval_count": "minimum_evaluations",
    "include_evals": "include_evaluations",
}
# ``--name`` selects the tool's entity and is exposed under the entity's name.
ENTITY_PARAMETERS: dict[str, str] = {
    "get_task": "task",
    "get_method": "method",
    "get_conference": "conference",
    "get_organization": "organization",
    "get_framework": "framework",
    "get_benchmark": "benchmark",
}
# Parameters with no CLI flag; ``read_paper`` continues with a signed cursor.
MCP_ONLY_PARAMETERS: dict[str, frozenset[str]] = {
    "read_paper": frozenset({"cursor"}),
    "get_paper_info": frozenset({"repo_limit"}),
    "get_task": frozenset({"benchmark_limit"}),
}
_CLI_DESTINATIONS = {mcp: cli for cli, mcp in PARAMETER_NAMES.items()}


@cache
def _cli_destinations(tool: str) -> frozenset[str]:
    return frozenset(queries.query_options(TOOL_COMMANDS[tool]))


def cli_options(tool: str, **parameters: Any) -> dict[str, Any]:
    """Translate MCP parameters into the CLI destinations of ``tool``.

    A parameter keeps its name when the command has that flag (``--limit``),
    and otherwise follows ``PARAMETER_NAMES`` (``limit`` -> ``--page-size``).
    """
    entity = ENTITY_PARAMETERS.get(tool)
    destinations = _cli_destinations(tool)
    options: dict[str, Any] = {}
    for name, value in parameters.items():
        if name in MCP_ONLY_PARAMETERS.get(tool, frozenset()):
            continue
        if name == entity:
            destination = "name"
        elif name in destinations:
            destination = name
        else:
            destination = _CLI_DESTINATIONS.get(name, name)
        options[destination] = value
    return options


Page = Annotated[int, Field(ge=1, le=100, description="Result page, starting at 1.")]
Limit = Annotated[
    int, Field(ge=1, le=MAX_ROWS, description="Rows per response, at most 25.")
]
Reference = Annotated[
    str,
    Field(
        min_length=1,
        max_length=500,
        description="arXiv ID, numeric PwC ID, arXiv/Hugging Face/PwC URL, or exact title.",
    ),
]
Query = Annotated[str, Field(min_length=1, max_length=500)]
Entity = Annotated[str, Field(min_length=1, max_length=500)]
IsoDate = Annotated[
    str, Field(max_length=10, description="Inclusive publication date, YYYY-MM-DD.")
]
AuthorList = Annotated[
    list[str],
    Field(
        max_length=10,
        description="Exact author names, numeric IDs, or @HF_USERNAME; every author must match.",
    ),
]
ResourceLimit = Annotated[int, Field(ge=1, le=10)]
Output = TypeVar("Output", bound=BaseModel)


def _tool_result(value: Output, markdown: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=markdown)],
        structured_content=value.model_dump(mode="json"),
    )


def _structured_model(result: Any, model: type[Output]) -> Output:
    if isinstance(result, CallToolResult):
        return model.model_validate(result.structured_content)
    return result


Area = Annotated[str, Field(description="Case-insensitive exact area name or area ID.")]
Direction = Literal["asc", "desc"]
ParameterSize = Annotated[
    str,
    Field(
        max_length=32,
        description="Inclusive model-size limit such as 500M, 1.5B, 3B, or a raw integer.",
    ),
]
MetricNames = Annotated[
    list[str],
    Field(
        min_length=1, description="Metric names that every returned row must report."
    ),
]
MetricBounds = Annotated[
    dict[str, float], Field(description="Metric name to numeric threshold.")
]
SortMetric = Annotated[
    str,
    Field(
        max_length=200,
        description="METRIC or METRIC:asc|desc; default direction is desc.",
    ),
]
ParetoObjectives = Annotated[
    list[str],
    Field(
        min_length=2,
        description="Two or more METRIC:higher or METRIC:lower objectives; keeps the Pareto frontier.",
    ),
]


def _validate_date_range(start: str | None, end: str | None) -> None:
    try:
        start_date = date.fromisoformat(start) if start else None
        end_date = date.fromisoformat(end) if end else None
    except ValueError as error:
        raise ToolError("publication dates must use YYYY-MM-DD") from error
    if start_date and end_date and start_date > end_date:
        raise ToolError("published_after must be on or before published_before")


def _dicts(values: Any) -> list[dict[str, Any]]:
    return [value for value in values or [] if isinstance(value, dict)]


def _next_page(data: Any) -> int | None:
    value = data.get("next_page") if isinstance(data, dict) else None
    return int(value) if value is not None else None


def _paper_page(data: Any) -> PaperPage:
    """Project a paper listing; recent/trending endpoints return a bare list."""
    if isinstance(data, list):
        rows: Any = data
    elif isinstance(data, dict):
        rows = data.get("results")
    else:
        raise TypeError("paper listing did not contain a result document")
    return PaperPage(
        items=[paper_summary(item) for item in _dicts(rows)],
        next_page=_next_page(data),
        data=data,
    )


def _grouped_benchmarks(areas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        benchmark
        for area in areas
        for task in _dicts(area.get("tasks"))
        for benchmark in _dicts(task.get("benchmarks"))
    ]


class Catalog(Protocol):
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

    def query(self, command: tuple[str, ...], options: Mapping[str, Any]) -> Any: ...


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
        description=(
            "Read-only access to papers, tasks, methods, conferences, organizations, "
            "frameworks, and benchmarks; every pwc CLI research command and flag."
        ),
        instructions=(
            "Use search_papers for relevance and list tools for deterministic filters. "
            "Pass slugs or numeric IDs returned by list tools to exact lookup tools. "
            "Dates use YYYY-MM-DD. Structured data is in structuredContent; text is a "
            "short Markdown summary. Official implementation means catalog-designated "
            "official code, not an independent audit. Evaluation is_open describes the "
            "cataloged implementation and may be null when unknown."
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

    def run(tool: str, **parameters: Any) -> Any:
        """Run the tool's CLI command; surface usage errors, hide upstream detail."""
        try:
            return catalog.query(TOOL_COMMANDS[tool], cli_options(tool, **parameters))
        except UsageError as error:
            raise ToolError(str(error)) from error
        except (ResponseError, TransportError) as error:
            raise ToolError(catalog_error_message(error)) from error

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def search_papers(
        query: Query,
        mode: Literal["hybrid", "keyword", "semantic"] = DEFAULT_SEARCH_MODE,
        page: Page = 1,
        limit: Limit = 10,
        published_after: IsoDate | None = None,
        published_before: IsoDate | None = None,
        has_official_implementation: bool = False,
    ) -> PaperPage:
        """Search papers by title, topic, author, or arXiv ID (`pwc search`). Broad discovery only: for best, top, or state-of-the-art model questions start with get_task, list_benchmarks, and get_benchmark, which return leaderboard evidence that search cannot. Mode defaults to hybrid like `pwc search`; use keyword for exact terminology or to stay under the semantic search rate limit."""
        _validate_date_range(published_after, published_before)
        result = _paper_page(
            run(
                "search_papers",
                query=query,
                mode=mode,
                page=page,
                limit=limit,
                published_after=published_after,
                published_before=published_before,
                has_official_implementation=has_official_implementation,
            )
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
        repo_limit: ResourceLimit = 5,
        include_evaluations: bool = False,
    ) -> PaperInfoResult:
        """Get compact paper metadata and official-first code. Set include_resources for additional repositories/Hugging Face artifacts, repo_limit to cap each list, and include_evaluations only for bounded legacy compatibility (`pwc paper info`)."""
        data = run(
            "get_paper_info",
            paper=paper,
            # Fetch once so the compact default can retain official code while
            # omitting hundreds of community repositories from the response.
            include_resources=True,
            include_evaluations=include_evaluations,
        )
        if not isinstance(data, dict):
            raise TypeError("paper response did not contain a paper")
        evaluations = data.get("evaluations")
        result = PaperInfoResult(
            paper=paper_detail(
                data, include_resources=include_resources, repo_limit=repo_limit
            ),
            evaluation_count=(
                int(evaluations.get("count") or 0)
                if isinstance(evaluations, dict)
                else None
            ),
            evaluations=(
                [paper_evaluation(item) for item in _dicts(evaluations.get("results"))]
                if isinstance(evaluations, dict)
                else None
            ),
            data=None,
        )
        return _tool_result(
            result,
            f"## {result.paper.title}\n\n{result.paper.code_repository_count} code repositories; returned {len(result.paper.repositories)}.",
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_paper_evaluations(
        paper: Reference, page: Page = 1, limit: Limit = 20
    ) -> EvaluationPage:
        """Get one page of benchmark evaluations for a paper, including protocol, source, openness, and task-scoped ranks (`pwc paper evaluations`)."""
        data = run("get_paper_evaluations", paper=paper, page=page, limit=limit)
        rows = _dicts(data.get("results")) if isinstance(data, dict) else []
        values = [evaluation(row) for row in rows]
        result = EvaluationPage(
            paper=paper,
            evaluation_count=int(data.get("count") or len(values)),
            page=page,
            next_page=_next_page(data),
            evaluations=values,
            data=None,
        )
        return _tool_result(
            result,
            f"Found {result.evaluation_count} evaluation rows; returned {len(values)} on page {page}."
            + (f" Next page: {result.next_page}." if result.next_page else ""),
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def read_paper(paper: Reference, cursor: str | None = None) -> PaperReadResult:
        """Read stored paper Markdown, continuing oversized documents with a cursor (`pwc paper read`). Pass the next_cursor value from the previous result together with the same paper (any reference to that paper works); omit cursor to start from the beginning."""
        reference = paper.strip()
        try:
            state = codec.decode(cursor, reference=reference) if cursor else None
        except CursorReferenceMismatch as error:
            # The agent continued with another spelling of the same paper, for
            # example the numeric catalog ID after starting from the arXiv ID.
            try:
                canonical = catalog.resolve_paper(reference)
            except (ResponseError, TransportError) as inner:
                raise ToolError(catalog_error_message(inner)) from inner
            if canonical != error.state.paper:
                raise ToolError(
                    "continuation cursor belongs to a different paper; omit cursor "
                    "to start reading this paper from the beginning"
                ) from error
            state = error.state
        except ValueError as error:
            raise ToolError(
                f"{error}; pass the next_cursor value from the previous read_paper "
                "result, or omit cursor to start from the beginning"
            ) from error
        if state is None:
            try:
                canonical = catalog.resolve_paper(reference)
            except (ResponseError, TransportError) as error:
                raise ToolError(catalog_error_message(error)) from error
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
        except HTTPStatusError as error:
            if error.status == 404:
                raise ToolError(
                    "no_markdown: no stored Markdown is available for this paper"
                ) from error
            raise ToolError(catalog_error_message(error)) from error
        except (ResponseError, TransportError) as error:
            raise ToolError(catalog_error_message(error)) from error
        if chunk.paper != canonical or chunk.source != source:
            raise ToolError("paper changed; restart reading from the beginning")
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
        return PaperReadResult(
            paper=reference,
            markdown=chunk.markdown,
            truncated=chunk.truncated,
            next_cursor=next_cursor,
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
        published_after: IsoDate | None = None,
        published_before: IsoDate | None = None,
        all_versions: bool = False,
        order_by: Literal["trending", "date_published", "citation_count"] = "trending",
        order_direction: Direction = "desc",
        include_resources: bool = False,
        has_official_implementation: bool = False,
        page: Page = 1,
        limit: Limit = 20,
    ) -> PaperPage:
        """List and filter papers by exact catalog associations in a deterministic order (`pwc paper list`)."""
        _validate_date_range(published_after, published_before)
        return _paper_page(
            run(
                "list_papers",
                search=search,
                task=task,
                method=method,
                conference=conference,
                framework=framework,
                organization=organization,
                authors=authors or None,
                published_after=published_after,
                published_before=published_before,
                all_versions=all_versions,
                order_by=order_by,
                order_direction=order_direction,
                include_resources=include_resources,
                has_official_implementation=has_official_implementation,
                page=page,
                limit=limit,
            )
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_recent_papers(limit: Limit = 10) -> PaperPage:
        """List the most recently added papers (`pwc paper recent`)."""
        return _paper_page(run("list_recent_papers", limit=limit))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_trending_papers(
        limit: Limit = 20,
        max_age_days: Annotated[int, Field(ge=1, le=365)] = 180,
        min_velocity: float | None = None,
    ) -> PaperPage:
        """List trending papers by repository velocity (`pwc paper trending`)."""
        return _paper_page(
            run(
                "list_trending_papers",
                limit=limit,
                max_age_days=max_age_days,
                min_velocity=min_velocity,
            )
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_related_papers(
        paper: Reference, limit: Annotated[int, Field(ge=1, le=20)] = 4
    ) -> PaperPage:
        """Find catalog papers related to one paper (`pwc paper related`)."""
        return _paper_page(run("get_related_papers", paper=paper, limit=limit))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_paper_lineage(paper: Reference) -> PaperLineageResult:
        """Get explicit predecessor and successor papers (`pwc paper lineage list`)."""
        data = run("get_paper_lineage", paper=paper)
        current = data.get("paper") if isinstance(data, dict) else None
        if not isinstance(current, dict):
            raise TypeError("lineage response did not contain a paper")
        return PaperLineageResult(
            paper=paper_reference(current),
            predecessors=[
                paper_reference(item) for item in _dicts(data.get("predecessors"))
            ],
            successors=[
                paper_reference(item) for item in _dicts(data.get("successors"))
            ],
            data=data,
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_task(task: Entity, benchmark_limit: ResourceLimit = 10) -> TaskResult:
        """Get one exact task by name, slug, or ID with its hierarchy, ranked benchmarks, common methods, and trending papers (`pwc task --name`). Start here for any question about the best or state-of-the-art models for a task, then inspect a leaderboard with get_benchmark."""
        data = run("get_task", task=task)
        item = data.get("task") if isinstance(data, dict) else None
        if not isinstance(item, dict):
            raise TypeError("task response did not contain a task")
        area_item = data.get("area")
        result = TaskResult(
            task=TaskDetail(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or "Unknown task"),
                slug=str(item.get("slug") or item.get("id") or ""),
                description=(
                    str(item["description"]) if item.get("description") else None
                ),
                paper_count=int(
                    item.get("paper_count") or data.get("paper_count") or 0
                ),
                area=(
                    AreaReference(
                        id=str(area_item.get("id") or ""),
                        name=str(area_item.get("name") or "Unknown area"),
                    )
                    if isinstance(area_item, dict)
                    else None
                ),
                parents=[catalog_reference(v) for v in _dicts(data.get("parents"))],
                children=[catalog_reference(v) for v in _dicts(data.get("children"))],
                benchmarks=[
                    benchmark_summary(v) for v in _dicts(data.get("benchmarks"))
                ][:benchmark_limit],
            ),
            data=None,
        )
        return _tool_result(
            result,
            f"## {result.task.name}\n\n{result.task.paper_count} papers; returned {len(result.task.benchmarks)} benchmarks.",
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_tasks(
        search: str | None = None,
        area: Area | None = None,
        level: int | None = None,
        visible_only: bool = False,
        group_by_area: bool = False,
        order_by: Literal["name", "created_at", "level", "paper_count"] = "name",
        order_direction: Direction = "asc",
        page: Page = 1,
        limit: Limit | None = None,
    ) -> QueryResult:
        """List and filter research tasks, or group the visible top-level taxonomy by area (`pwc task list`)."""
        return QueryResult(
            data=run(
                "list_tasks",
                search=search,
                area=area,
                level=level,
                visible_only=visible_only,
                group_by_area=group_by_area,
                order_by=order_by,
                order_direction=order_direction,
                page=page,
                limit=limit if limit is not None or group_by_area else MAX_ROWS,
            )
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_method(method: Entity) -> MethodResult:
        """Get one exact method by name, full name, slug, or ID (`pwc method --name`)."""
        data = run("get_method", method=method)
        item = data.get("method") if isinstance(data, dict) else None
        if not isinstance(item, dict):
            raise TypeError("method response did not contain a method")
        result = MethodResult(
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
            ),
            data=None,
        )
        return _tool_result(
            result, f"## {result.method.name}\n\n{result.method.paper_count} papers."
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_methods(
        search: str | None = None,
        area: Area | None = None,
        introduced_year: int | None = None,
        order_by: Literal[
            "name", "full_name", "introduced_year", "created_at", "paper_count"
        ] = "name",
        order_direction: Direction = "asc",
        page: Page = 1,
        limit: Limit = MAX_ROWS,
    ) -> QueryResult:
        """List and filter research methods (`pwc method list`)."""
        return QueryResult(
            data=run(
                "list_methods",
                search=search,
                area=area,
                introduced_year=introduced_year,
                order_by=order_by,
                order_direction=order_direction,
                page=page,
                limit=limit,
            )
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_conference(conference: Entity) -> QueryResult:
        """Get one exact conference by name, slug, or ID (`pwc conference --name`)."""
        return QueryResult(data=run("get_conference", conference=conference))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_conferences(year: int | None = None) -> QueryResult:
        """List conferences with imported papers (`pwc conference list`)."""
        return QueryResult(data=run("list_conferences", year=year))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_organization(organization: Entity) -> QueryResult:
        """Get one exact research organization by name, slug, or ID (`pwc organization --name`)."""
        return QueryResult(data=run("get_organization", organization=organization))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_organizations(featured_only: bool = False) -> QueryResult:
        """List research organizations (`pwc organization list`)."""
        return QueryResult(data=run("list_organizations", featured_only=featured_only))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_framework(framework: Entity) -> QueryResult:
        """Get one exact research framework by name, slug, or ID (`pwc framework --name`)."""
        return QueryResult(data=run("get_framework", framework=framework))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_frameworks(
        domain: str | None = None,
        category: str | None = None,
        platform: str | None = None,
    ) -> QueryResult:
        """List research frameworks by domain, category, or platform (`pwc framework list`)."""
        return QueryResult(
            data=run(
                "list_frameworks", domain=domain, category=category, platform=platform
            )
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_benchmark(
        benchmark: Entity,
        page: Page = 1,
        limit: Limit = 20,
        is_open: bool | None = None,
        max_parameters: ParameterSize | None = None,
        require_metrics: MetricNames | None = None,
        minimum_metrics: MetricBounds | None = None,
        maximum_metrics: MetricBounds | None = None,
        sort_metric: SortMetric | None = None,
        pareto: ParetoObjectives | None = None,
    ) -> BenchmarkResult:
        """Get one exact benchmark and its leaderboard (`pwc benchmark --name`). Use max_parameters (for example "4B") to keep models at or below a size, sort_metric to rank by a metric, and minimum_metrics, maximum_metrics, require_metrics, or pareto to select rows; matched_count reports how many rows passed before limit. Metric names are matched case-insensitively, through common aliases (AP/mAP, top1/Accuracy, AUROC/AUC, Pass@1/Pass Rate), and by the one leaderboard metric containing the request or an alias of it (Normalized Score for "D4RL Normalized Score", GenEval Score for "Overall"); an unknown metric error lists the leaderboard's actual metric names."""
        data = run(
            "get_benchmark",
            benchmark=benchmark,
            page=page,
            limit=limit,
            is_open=is_open,
            max_parameters=max_parameters,
            require_metrics=require_metrics,
            minimum_metrics=minimum_metrics,
            maximum_metrics=maximum_metrics,
            sort_metric=sort_metric,
            pareto=pareto,
        )
        item = data.get("benchmark") if isinstance(data, dict) else None
        if not isinstance(item, dict):
            raise TypeError("benchmark response did not contain a benchmark")
        matched = data.get("matched_count")
        evaluations = [evaluation(value) for value in _dicts(data.get("results"))]
        result = BenchmarkResult(
            benchmark=benchmark_summary(item),
            evaluation_count=int(data.get("count") or 0),
            matched_count=int(matched) if matched is not None else None,
            evaluations=evaluations,
            metric_directions=metric_directions(evaluations),
            page=page,
            next_page=_next_page(data),
            data=None,
        )
        return _tool_result(
            result,
            f"## {result.benchmark.name}\n\n{result.evaluation_count} evaluation rows; returned {len(result.evaluations)} models on page {page}. Ranks are task-scoped."
            + (f" Next page: {result.next_page}." if result.next_page else ""),
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_benchmarks(
        search: str | None = None,
        task: str | None = None,
        include_descendants: bool = False,
        minimum_evaluations: int | None = None,
        is_open: bool | None = None,
        group_by_area: bool = False,
        area: Area | None = None,
        benchmarks_per_task: Annotated[int, Field(ge=1, le=10)] = 3,
        order_by: Literal["trending", "name", "full_name", "created_at", "paper_count"]
        | None = None,
        order_direction: Direction = "asc",
        page: Page = 1,
        limit: Limit | None = None,
    ) -> BenchmarkPage:
        """List benchmarks for a task ranked by trend, filter them, or group them by area and task (`pwc benchmark list`). order_by=trending needs task (without it the list is ordered by name); area groups a whole area and is dropped when combined with task, search, or ordering filters. Follow with get_benchmark on the most relevant leaderboard."""
        notes: list[str] = []
        if order_by == "trending" and not task:
            order_by = None
            notes.append("order_by=trending needs task; ordered by name instead")
        filtered = bool(
            task
            or search
            or include_descendants
            or is_open is not None
            or order_by is not None
            or order_direction != "asc"
        )
        if area is not None and filtered:
            area = None
            notes.append("area cannot be combined with filters; returned the filtered list")
        grouped = group_by_area or area is not None
        # Grouped listings are not paginated; the CLI rejects page/limit there.
        data = run(
            "list_benchmarks",
            search=search,
            task=task,
            include_descendants=include_descendants,
            minimum_evaluations=minimum_evaluations,
            is_open=is_open,
            group_by_area=group_by_area,
            area=area,
            benchmarks_per_task=benchmarks_per_task,
            order_by=order_by,
            order_direction=order_direction,
            page=1 if grouped else page,
            limit=None if grouped else (limit if limit is not None else MAX_ROWS),
        )
        if not isinstance(data, dict):
            raise TypeError("benchmark listing did not contain a result document")
        rows = _dicts(data.get("results"))
        if grouped:
            rows = _grouped_benchmarks(rows)
        result = BenchmarkPage(
            items=[benchmark_summary(item) for item in rows],
            next_page=_next_page(data),
            data=data,
        )
        summary = f"Found {len(result.items)} benchmarks."
        if result.next_page:
            summary += f" Next page: {result.next_page}."
        for note in notes:
            summary += f" Note: {note}."
        return _tool_result(result, summary)

    @server.prompt(name="find_papers", title="Find papers")
    def find_papers_prompt(topic: str) -> str:
        return (
            f"Find papers about {topic!r}. Use semantic search for concepts, then "
            "inspect the most relevant papers and cite their canonical URLs."
        )

    @server.prompt(name="compare_leaderboard", title="Compare a leaderboard")
    def compare_leaderboard_prompt(benchmark: str) -> str:
        return (
            f"Inspect the {benchmark!r} benchmark. Explain metric direction and "
            "evaluation settings, and do not compare ranks from different task scopes."
        )

    @server.prompt(name="survey_task", title="Survey a research task")
    def survey_task_prompt(task: str) -> str:
        return (
            f"Survey the {task!r} task. Resolve its slug, inspect high-coverage "
            "benchmarks, then summarize representative papers and methods."
        )

    @server.resource(
        "pwc://papers/{paper}",
        name="paper-info",
        title="Paper information",
        description="Canonical Papers With Code paper metadata.",
        mime_type="application/json",
    )
    def paper_info_resource(paper: str) -> str:
        return _structured_model(
            get_paper_info(paper), PaperInfoResult
        ).model_dump_json()

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
        return _structured_model(get_task(task), TaskResult).model_dump_json()

    @server.resource(
        "pwc://benchmarks/{benchmark}",
        name="benchmark",
        title="Benchmark leaderboard",
        description="Canonical benchmark metadata and leading evaluations.",
        mime_type="application/json",
    )
    def benchmark_resource(benchmark: str) -> str:
        return _structured_model(
            get_benchmark(benchmark), BenchmarkResult
        ).model_dump_json()

    return server
