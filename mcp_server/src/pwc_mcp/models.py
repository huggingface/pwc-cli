from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryResult(OutputModel):
    """The exact ``data`` document that ``pwc <command> --json`` prints."""

    schema_version: Literal["v1"] = "v1"
    data: Any = Field(
        default=None,
        description="Complete CLI JSON payload for this call; typed fields are projections of it.",
    )


class PaperSummary(OutputModel):
    id: str
    arxiv_id: str | None = None
    title: str
    authors: list[str]
    published: str | None = None
    citation_count: int | None = None
    url: str | None = None
    has_official_implementation: bool
    code_repository_count: int


class PaperPage(QueryResult):
    items: list[PaperSummary]
    next_page: int | None = None


class CatalogReference(OutputModel):
    id: str
    name: str
    slug: str | None = None


class RepositoryReference(OutputModel):
    url: str
    is_official: bool


class PaperDetail(OutputModel):
    id: str
    arxiv_id: str | None = None
    title: str
    abstract: str | None = None
    authors: list[str]
    published: str | None = None
    citation_count: int | None = None
    url: str | None = None
    pdf_url: str | None = None
    tasks: list[CatalogReference]
    methods: list[CatalogReference]
    repositories: list[RepositoryReference]
    project_pages: list[str]
    hf_models: list[str] = []
    hf_datasets: list[str] = []
    hf_spaces: list[str] = []


class PaperEvaluation(OutputModel):
    id: str
    benchmark: str | None = None
    task: str | None = None
    model_name: str
    harness: str | None = None
    metrics: dict[str, float | int | str | None]
    best_metric: str | None = None
    best_rank: int | None = None
    is_open: bool
    num_parameters: int | None = None
    source_url: str | None = None


class PaperInfoResult(QueryResult):
    paper: PaperDetail
    evaluation_count: int | None = None
    evaluations: list[PaperEvaluation] | None = None


class PaperReadResult(OutputModel):
    schema_version: Literal["v1"] = "v1"
    paper: str
    markdown: str
    truncated: bool
    next_cursor: str | None = None


class PaperReference(OutputModel):
    id: str
    reference: str | None = None
    title: str


class PaperLineageResult(QueryResult):
    paper: PaperReference
    predecessors: list[PaperReference]
    successors: list[PaperReference]


class AreaReference(OutputModel):
    id: str
    name: str


class BenchmarkSummary(OutputModel):
    id: str
    name: str
    slug: str | None = None
    full_name: str | None = None
    description: str | None = None
    hf_url: str | None = None
    paper_count: int


class TaskDetail(OutputModel):
    id: str
    name: str
    slug: str
    description: str | None = None
    paper_count: int
    area: AreaReference | None = None
    parents: list[CatalogReference]
    children: list[CatalogReference]
    benchmarks: list[BenchmarkSummary]


class TaskResult(QueryResult):
    task: TaskDetail


class MethodDetail(OutputModel):
    id: str
    name: str
    slug: str
    full_name: str | None = None
    description: str | None = None
    introduced_year: int | None = None
    source_paper_id: str | None = None
    source_url: str | None = None
    source_title: str | None = None
    paper_count: int


class MethodResult(QueryResult):
    method: MethodDetail


class BenchmarkPage(QueryResult):
    items: list[BenchmarkSummary]
    next_page: int | None = None


class Evaluation(OutputModel):
    id: str
    model_name: str
    harness: str | None = None
    metrics: dict[str, float | int | str | None]
    best_metric: str | None = None
    best_rank: int | None = None
    task: str | None = None
    paper_id: str | None = None
    paper_title: str | None = None
    paper_arxiv_id: str | None = None
    paper_published: str | None = None
    is_open: bool
    num_parameters: int | None = None


class BenchmarkResult(QueryResult):
    benchmark: BenchmarkSummary
    evaluation_count: int
    matched_count: int | None = None
    evaluations: list[Evaluation]


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _urls(values: Any) -> list[str]:
    urls = []
    for value in values or []:
        url = value.get("url") if isinstance(value, dict) else value
        if url:
            urls.append(str(url))
    return urls


def paper_summary(item: dict[str, Any]) -> PaperSummary:
    return PaperSummary(
        id=str(item.get("id") or ""),
        arxiv_id=_text(item.get("arxiv_id")),
        title=str(item.get("title") or "Untitled paper"),
        authors=[str(author) for author in item.get("authors") or []],
        published=_text(item.get("published") or item.get("date_published")),
        citation_count=_int(item.get("citation_count")),
        url=_text(item.get("url_abs") or item.get("source_url")),
        has_official_implementation=item.get("has_official_implementation") is True,
        code_repository_count=int(item.get("code_repository_count") or 0),
    )


def catalog_reference(item: dict[str, Any]) -> CatalogReference:
    return CatalogReference(
        id=str(item.get("id") or ""),
        name=str(item.get("name") or item.get("slug") or "Unknown"),
        slug=_text(item.get("slug")),
    )


def paper_detail(item: dict[str, Any]) -> PaperDetail:
    repositories = []
    for repository in item.get("repositories") or []:
        if isinstance(repository, dict) and repository.get("url"):
            repositories.append(
                RepositoryReference(
                    url=str(repository["url"]),
                    is_official=repository.get("is_official") is True,
                )
            )
    return PaperDetail(
        id=str(item.get("id") or ""),
        arxiv_id=_text(item.get("arxiv_id")),
        title=str(item.get("title") or "Untitled paper"),
        abstract=_text(item.get("abstract")),
        authors=[str(author) for author in item.get("authors") or []],
        published=_text(item.get("published")),
        citation_count=_int(item.get("citation_count")),
        url=_text(item.get("url_abs") or item.get("source_url")),
        pdf_url=_text(item.get("url_pdf")),
        tasks=[
            catalog_reference(task)
            for task in item.get("tasks") or []
            if isinstance(task, dict)
        ],
        methods=[
            catalog_reference(method)
            for method in item.get("methods") or []
            if isinstance(method, dict)
        ],
        repositories=repositories,
        project_pages=_urls(item.get("project_pages")),
        hf_models=_urls(item.get("hf_models")),
        hf_datasets=_urls(item.get("hf_datasets")),
        hf_spaces=_urls(item.get("hf_spaces")),
    )


def _metrics(item: dict[str, Any]) -> dict[str, float | int | str | None]:
    metrics = item.get("metrics")
    if not isinstance(metrics, dict):
        return {}
    return {str(key): value for key, value in metrics.items()}


def paper_evaluation(item: dict[str, Any]) -> PaperEvaluation:
    return PaperEvaluation(
        id=str(item.get("id") or ""),
        benchmark=_text(item.get("dataset_name")),
        task=_text(item.get("task_name")),
        model_name=str(item.get("model_name") or "Unknown model"),
        harness=_text(item.get("harness")),
        metrics=_metrics(item),
        best_metric=_text(item.get("best_metric")),
        best_rank=_int(item.get("best_rank")),
        is_open=item.get("is_open") is not False,
        num_parameters=_int(item.get("num_parameters")),
        source_url=_text(item.get("result_url") or item.get("source_url")),
    )


def paper_reference(item: dict[str, Any]) -> PaperReference:
    reference = item.get("reference") or item.get("arxiv_id")
    return PaperReference(
        id=str(item.get("id") or ""),
        reference=str(reference) if reference else None,
        title=str(item.get("title") or item.get("reference") or "Untitled paper"),
    )


def benchmark_summary(item: dict[str, Any]) -> BenchmarkSummary:
    count = (
        item.get("paper_count")
        if item.get("paper_count") is not None
        else item.get("all_time_paper_count", item.get("evaluation_count"))
    )
    return BenchmarkSummary(
        id=str(item.get("id") or ""),
        name=str(item.get("name") or item.get("slug") or "Unknown benchmark"),
        slug=_text(item.get("slug")),
        full_name=_text(item.get("full_name")),
        description=_text(item.get("description")),
        hf_url=_text(item.get("hf_url")),
        paper_count=_int(count) or 0,
    )


def evaluation(item: dict[str, Any]) -> Evaluation:
    return Evaluation(
        id=str(item.get("id") or ""),
        model_name=str(item.get("model_name") or "Unknown model"),
        harness=_text(item.get("harness")),
        metrics=_metrics(item),
        best_metric=_text(item.get("best_metric")),
        best_rank=_int(item.get("best_rank")),
        task=_text(item.get("task_name")),
        paper_id=_text(item.get("paper_id")),
        paper_title=_text(item.get("paper_title")),
        paper_arxiv_id=_text(item.get("paper_arxiv_id")),
        paper_published=_text(item.get("paper_published_date")),
        is_open=item.get("is_open") is not False,
        num_parameters=_int(item.get("num_parameters")),
    )
