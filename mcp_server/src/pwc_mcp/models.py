from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class PaperPage(OutputModel):
    schema_version: Literal["v1"] = "v1"
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


class PaperInfoResult(OutputModel):
    schema_version: Literal["v1"] = "v1"
    paper: PaperDetail


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


class PaperLineageResult(OutputModel):
    schema_version: Literal["v1"] = "v1"
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


class TaskResult(OutputModel):
    schema_version: Literal["v1"] = "v1"
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


class MethodResult(OutputModel):
    schema_version: Literal["v1"] = "v1"
    method: MethodDetail


class BenchmarkPage(OutputModel):
    schema_version: Literal["v1"] = "v1"
    items: list[BenchmarkSummary]
    next_page: int | None = None


class Evaluation(OutputModel):
    id: str
    model_name: str
    metrics: dict[str, float | int | str | None]
    best_rank: int | None = None
    paper_id: str | None = None
    paper_title: str | None = None
    paper_arxiv_id: str | None = None
    is_open: bool
    num_parameters: int | None = None


class BenchmarkResult(OutputModel):
    schema_version: Literal["v1"] = "v1"
    benchmark: BenchmarkSummary
    evaluation_count: int
    evaluations: list[Evaluation]


def paper_summary(item: dict[str, Any]) -> PaperSummary:
    return PaperSummary(
        id=str(item.get("id") or ""),
        arxiv_id=str(item["arxiv_id"]) if item.get("arxiv_id") else None,
        title=str(item.get("title") or "Untitled paper"),
        authors=[str(author) for author in item.get("authors") or []],
        published=str(item["published"]) if item.get("published") else None,
        citation_count=(
            int(item["citation_count"])
            if item.get("citation_count") is not None
            else None
        ),
        url=str(item.get("url_abs") or item.get("source_url") or "") or None,
        has_official_implementation=item.get("has_official_implementation") is True,
        code_repository_count=int(item.get("code_repository_count") or 0),
    )


def catalog_reference(item: dict[str, Any]) -> CatalogReference:
    return CatalogReference(
        id=str(item.get("id") or ""),
        name=str(item.get("name") or item.get("slug") or "Unknown"),
        slug=str(item["slug"]) if item.get("slug") else None,
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
    project_pages = []
    for page in item.get("project_pages") or []:
        url = page.get("url") if isinstance(page, dict) else page
        if url:
            project_pages.append(str(url))
    return PaperDetail(
        id=str(item.get("id") or ""),
        arxiv_id=str(item["arxiv_id"]) if item.get("arxiv_id") else None,
        title=str(item.get("title") or "Untitled paper"),
        abstract=str(item["abstract"]) if item.get("abstract") else None,
        authors=[str(author) for author in item.get("authors") or []],
        published=str(item["published"]) if item.get("published") else None,
        citation_count=(
            int(item["citation_count"])
            if item.get("citation_count") is not None
            else None
        ),
        url=str(item.get("url_abs") or item.get("source_url") or "") or None,
        pdf_url=str(item["url_pdf"]) if item.get("url_pdf") else None,
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
        project_pages=project_pages,
    )


def paper_reference(item: dict[str, Any]) -> PaperReference:
    return PaperReference(
        id=str(item.get("id") or ""),
        reference=(
            str(item.get("reference") or item.get("arxiv_id"))
            if item.get("reference") or item.get("arxiv_id")
            else None
        ),
        title=str(item.get("title") or item.get("reference") or "Untitled paper"),
    )


def benchmark_summary(item: dict[str, Any]) -> BenchmarkSummary:
    return BenchmarkSummary(
        id=str(item.get("id") or ""),
        name=str(item.get("name") or item.get("slug") or "Unknown benchmark"),
        slug=str(item["slug"]) if item.get("slug") else None,
        full_name=str(item["full_name"]) if item.get("full_name") else None,
        description=str(item["description"]) if item.get("description") else None,
        hf_url=str(item["hf_url"]) if item.get("hf_url") else None,
        paper_count=int(item.get("paper_count") or 0),
    )


def evaluation(item: dict[str, Any]) -> Evaluation:
    metrics = item.get("metrics")
    return Evaluation(
        id=str(item.get("id") or ""),
        model_name=str(item.get("model_name") or "Unknown model"),
        metrics={str(key): value for key, value in metrics.items()}
        if isinstance(metrics, dict)
        else {},
        best_rank=int(item["best_rank"]) if item.get("best_rank") is not None else None,
        paper_id=str(item["paper_id"]) if item.get("paper_id") else None,
        paper_title=str(item["paper_title"]) if item.get("paper_title") else None,
        paper_arxiv_id=(
            str(item["paper_arxiv_id"]) if item.get("paper_arxiv_id") else None
        ),
        is_open=item.get("is_open") is not False,
        num_parameters=(
            int(item["num_parameters"])
            if isinstance(item.get("num_parameters"), int)
            and not isinstance(item.get("num_parameters"), bool)
            else None
        ),
    )
