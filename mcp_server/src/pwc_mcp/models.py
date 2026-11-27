from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

RELATIVE_MARKDOWN_LINK = re.compile(r"\]\((/[^)\s]*)\)")


def _absolute_links(value: str | None) -> str | None:
    if not value:
        return value
    return RELATIVE_MARKDOWN_LINK.sub(r"](https://paperswithcode.co\1)", value)


# Catalog descriptions link site pages relatively; agents need absolute URLs.
SiteMarkdown = Annotated[str | None, AfterValidator(_absolute_links)]


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _absolute_url(value: object) -> str | None:
    if not value:
        return None
    url = str(value)
    return f"https://paperswithcode.co{url}" if url.startswith("/") else url


class ToolResult(OutputModel):
    schema_version: Literal["v1"] = "v1"


class QueryResult(ToolResult):
    """The exact ``data`` document that ``pwc <command> --json`` prints."""

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
    has_official_implementation: bool = Field(
        description=(
            "True when the catalog marks at least one linked repository as the "
            "paper's official implementation; this is not an independent code audit."
        )
    )
    code_repository_count: int


class PaperLink(OutputModel):
    label: str
    url: str
    kind: str | None = None


AVAILABLE_AT = (
    "Where a paper outside the catalog can be read (abstract page, PDF, Hugging "
    "Face Papers, DOI, code); link to these, not to a Papers With Code paper page."
)


class PaperPage(QueryResult):
    items: list[PaperSummary]
    next_page: int | None = None
    note: str | None = Field(
        default=None,
        description="Set when the requested paper is not in the catalog and the items are a search fallback.",
    )
    available_at: list[PaperLink] | None = Field(default=None, description=AVAILABLE_AT)


class CatalogReference(OutputModel):
    id: str
    name: str
    slug: str | None = None


class RepositoryReference(OutputModel):
    url: str
    is_official: bool


class XThreadReference(OutputModel):
    url: str
    author: str
    handle: str


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
    code_repository_count: int
    tasks: list[CatalogReference]
    methods: list[CatalogReference]
    repositories: list[RepositoryReference]
    project_pages: list[str]
    hf_models: list[str] = []
    hf_datasets: list[str] = []
    hf_spaces: list[str] = []
    x_threads: list[XThreadReference] = []


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


class PaperInfoResult(ToolResult):
    paper: PaperDetail
    evaluation_count: int | None = None
    evaluations: list[PaperEvaluation] | None = None
    in_catalog: bool = Field(
        default=True,
        description="False when the paper is not indexed on Papers With Code; the metadata then comes from public Hugging Face Papers, arXiv, or Crossref records.",
    )
    note: str | None = None
    available_at: list[PaperLink] | None = Field(default=None, description=AVAILABLE_AT)


class PaperReadResult(ToolResult):
    paper: str
    markdown: str
    truncated: bool
    next_cursor: str | None = None
    in_catalog: bool = Field(
        default=True,
        description="False when the paper is not indexed on Papers With Code; markdown then holds public metadata and the abstract, not the full text.",
    )
    available_at: list[PaperLink] | None = Field(default=None, description=AVAILABLE_AT)


class PaperReference(OutputModel):
    id: str
    reference: str | None = None
    title: str


class PaperLineageResult(ToolResult):
    paper: PaperReference
    predecessors: list[PaperReference]
    successors: list[PaperReference]
    coverage: Literal["explicit_catalog_links_only"] = "explicit_catalog_links_only"
    coverage_note: str = (
        "Only explicit catalog relationships are returned; an empty list does not "
        "prove that no predecessor or successor exists."
    )


class AreaReference(OutputModel):
    id: str
    name: str


class BenchmarkSummary(OutputModel):
    id: str
    name: str
    slug: str | None = None
    full_name: str | None = None
    description: SiteMarkdown = None
    split: str | None = None
    hf_url: str | None = None
    paper_count: int


class TaskDetail(OutputModel):
    id: str
    name: str
    slug: str
    description: SiteMarkdown = None
    paper_count: int
    area: AreaReference | None = None
    parents: list[CatalogReference]
    children: list[CatalogReference]
    benchmarks: list[BenchmarkSummary]


class TaskResult(ToolResult):
    task: TaskDetail


class MethodDetail(OutputModel):
    id: str
    name: str
    slug: str
    full_name: str | None = None
    description: SiteMarkdown = None
    introduced_year: int | None = None
    source_paper_id: str | None = None
    source_url: str | None = None
    source_title: str | None = None
    paper_count: int


class MethodResult(ToolResult):
    method: MethodDetail


class TaskSummary(OutputModel):
    id: str
    name: str
    slug: str | None = None
    description: SiteMarkdown = None
    level: int | None = None
    area_id: str | None = None
    parent_id: str | None = None
    paper_count: int
    benchmark_count: int | None = None


class TaskAreaGroup(OutputModel):
    id: str
    name: str
    tasks: list[TaskSummary]


class TaskPage(QueryResult):
    items: list[TaskSummary]
    groups: list[TaskAreaGroup] | None = Field(
        default=None, description="Visible tasks by area when group_by_area is set; items is then empty."
    )
    next_page: int | None = None


class MethodSummary(OutputModel):
    id: str
    name: str
    slug: str | None = None
    full_name: str | None = None
    description: SiteMarkdown = None
    introduced_year: int | None = None
    source_paper_id: str | None = None
    paper_count: int


class MethodPage(QueryResult):
    items: list[MethodSummary]
    next_page: int | None = None


class ConferenceSummary(OutputModel):
    id: str
    name: str
    slug: str | None = None
    short_name: str | None = None
    year: int | None = None
    start_date: str | None = None
    end_date: str | None = None
    location: str | None = None
    url: str | None = Field(default=None, description="The conference's own website.")
    tier: str | None = None
    paper_count: int


class ConferenceDetail(ConferenceSummary):
    description: SiteMarkdown = None
    other_editions: list[str] = []


class ConferenceResult(QueryResult):
    conference: ConferenceDetail


class ConferencePage(QueryResult):
    items: list[ConferenceSummary]
    next_page: int | None = None


class OrganizationSummary(OutputModel):
    id: str
    name: str
    slug: str | None = None
    paper_count: int
    is_featured: bool = False
    trending_rank: int | None = None
    first_paper_year: int | None = None
    latest_paper_year: int | None = None
    url: str | None = Field(default=None, description="Papers With Code organization page.")


class OrganizationDetail(OrganizationSummary):
    description: SiteMarkdown = None
    website_url: str | None = None
    github_url: str | None = None
    hf_url: str | None = None
    top_trending_paper: PaperReference | None = None


class OrganizationResult(QueryResult):
    organization: OrganizationDetail


class OrganizationPage(QueryResult):
    items: list[OrganizationSummary]
    next_page: int | None = None


class FrameworkSummary(OutputModel):
    id: str
    name: str
    slug: str | None = None
    organization_name: str | None = None
    description: SiteMarkdown = None
    when_to_use: str | None = None
    platforms: list[str] = []
    domain_slug: str | None = None
    domain_name: str | None = None
    category_slug: str | None = None
    category_name: str | None = None
    github_url: str | None = None
    docs_url: str | None = None
    introducing_paper_arxiv_id: str | None = None
    paper_count: int


class FrameworkResult(QueryResult):
    framework: FrameworkSummary


class FrameworkPage(QueryResult):
    items: list[FrameworkSummary]
    next_page: int | None = None


class BenchmarkPage(QueryResult):
    items: list[BenchmarkSummary]
    next_page: int | None = None
    note: str | None = Field(
        default=None,
        description="Set when an argument was dropped or changed; explains what was returned instead.",
    )


class EvaluationRankScope(OutputModel):
    task_id: str | None = None
    task_name: str | None = None
    task_slug: str | None = None
    rank: int | None = None


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
    rank_scopes: list[EvaluationRankScope] = Field(default_factory=list)
    is_open: bool | None = Field(
        description=(
            "Catalog openness flag for the evaluated implementation: true=open, "
            "false=closed, null=not recorded."
        )
    )
    num_parameters: int | None = None
    split: str | None = None
    shots: int | None = None
    evaluation_protocol: str | None = None
    source_url: str | None = None
    code_url: str | None = None
    hf_model_url: str | None = None
    updated_at: str | None = None
    uses_additional_data: bool | None = None


class BenchmarkResult(ToolResult):
    benchmark: BenchmarkSummary
    evaluation_count: int
    matched_count: int | None = None
    evaluations: list[Evaluation]
    metric_directions: dict[str, Literal["higher", "lower", "unknown"]]
    page: int
    next_page: int | None = None
    metric_note: str | None = Field(
        default=None,
        description="Set when a requested metric is not on this leaderboard; the rows are then unfiltered and the note lists the available metrics.",
    )
    ranking_note: str = (
        "Ranks are scoped by task and are not necessarily comparable across "
        "rank_scopes."
    )


class EvaluationPage(ToolResult):
    paper: str
    evaluation_count: int
    page: int
    next_page: int | None = None
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
        id=str(item.get("id") or item.get("paper_id") or ""),
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


def paper_detail(
    item: dict[str, Any], *, include_resources: bool = False, repo_limit: int = 5
) -> PaperDetail:
    repositories = []
    for repository in item.get("repositories") or []:
        if isinstance(repository, dict) and repository.get("url"):
            value = RepositoryReference(
                url=str(repository["url"]),
                is_official=repository.get("is_official") is True,
            )
            if include_resources or value.is_official:
                repositories.append(value)
    repositories.sort(key=lambda repository: not repository.is_official)
    repositories = repositories[:repo_limit]
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
        code_repository_count=int(item.get("code_repository_count") or 0),
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
        project_pages=_urls(item.get("project_pages"))[:repo_limit]
        if include_resources
        else [],
        hf_models=_urls(item.get("hf_models"))[:repo_limit]
        if include_resources
        else [],
        hf_datasets=_urls(item.get("hf_datasets"))[:repo_limit]
        if include_resources
        else [],
        hf_spaces=_urls(item.get("hf_spaces"))[:repo_limit]
        if include_resources
        else [],
        x_threads=[
            XThreadReference(
                url=str(thread["url"]),
                author=str(thread.get("author_name") or ""),
                handle=str(thread.get("author_handle") or ""),
            )
            for thread in item.get("x_threads") or []
            if isinstance(thread, dict) and thread.get("url")
        ][:repo_limit]
        if include_resources
        else [],
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
        split=_text(item.get("split_name") or item.get("split")),
        hf_url=_text(item.get("hf_url")),
        paper_count=_int(count) or 0,
    )


def task_summary(item: dict[str, Any]) -> TaskSummary:
    return TaskSummary(
        id=str(item.get("id") or ""),
        name=str(item.get("name") or item.get("slug") or "Unknown task"),
        slug=_text(item.get("slug")),
        description=_text(item.get("description")),
        level=_int(item.get("level")),
        area_id=_text(item.get("area_id")),
        parent_id=_text(item.get("parent_id")),
        paper_count=_int(item.get("paper_count")) or 0,
        benchmark_count=_int(item.get("benchmark_count")),
    )


def task_area_group(item: dict[str, Any]) -> TaskAreaGroup:
    return TaskAreaGroup(
        id=str(item.get("id") or ""),
        name=str(item.get("name") or "Unknown area"),
        tasks=[task_summary(task) for task in item.get("tasks") or [] if isinstance(task, dict)],
    )


def method_summary(item: dict[str, Any]) -> MethodSummary:
    return MethodSummary(
        id=str(item.get("id") or ""),
        name=str(item.get("name") or item.get("slug") or "Unknown method"),
        slug=_text(item.get("slug")),
        full_name=_text(item.get("full_name")),
        description=_text(item.get("description")),
        introduced_year=_int(item.get("introduced_year")),
        source_paper_id=_text(item.get("source_paper_id")),
        paper_count=_int(item.get("paper_count")) or 0,
    )


def conference_summary(item: dict[str, Any]) -> ConferenceSummary:
    return ConferenceSummary(**_conference_fields(item))


def conference_detail(item: dict[str, Any]) -> ConferenceDetail:
    return ConferenceDetail(
        **_conference_fields(item),
        description=_text(item.get("description")),
        other_editions=[str(edition) for edition in item.get("other_editions") or []],
    )


def _conference_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or item.get("slug") or "Unknown conference"),
        "slug": _text(item.get("slug")),
        "short_name": _text(item.get("short_name")),
        "year": _int(item.get("year")),
        "start_date": _text(item.get("start_date")),
        "end_date": _text(item.get("end_date")),
        "location": _text(item.get("location") or item.get("venue")),
        "url": _text(item.get("url")),
        "tier": _text(item.get("tier")),
        "paper_count": _int(item.get("paper_count")) or 0,
    }


def organization_summary(item: dict[str, Any]) -> OrganizationSummary:
    return OrganizationSummary(**_organization_fields(item))


def organization_detail(item: dict[str, Any]) -> OrganizationDetail:
    top = item.get("top_trending_paper")
    return OrganizationDetail(
        **_organization_fields(item),
        description=_text(item.get("description")),
        website_url=_text(item.get("website_url")),
        github_url=_text(item.get("github_url")),
        hf_url=_text(item.get("hf_url")),
        top_trending_paper=paper_reference(top) if isinstance(top, dict) else None,
    )


def _organization_fields(item: dict[str, Any]) -> dict[str, Any]:
    slug = _text(item.get("slug"))
    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or slug or "Unknown organization"),
        "slug": slug,
        "paper_count": _int(item.get("paper_count")) or 0,
        "is_featured": item.get("is_featured") is True,
        "trending_rank": _int(item.get("trending_rank")),
        "first_paper_year": _int(item.get("first_paper_year")),
        "latest_paper_year": _int(item.get("latest_paper_year")),
        "url": f"https://paperswithcode.co/orgs/{key}" if (key := slug or _text(item.get("id"))) else None,
    }


def framework_summary(item: dict[str, Any]) -> FrameworkSummary:
    return FrameworkSummary(
        id=str(item.get("id") or ""),
        name=str(item.get("name") or item.get("slug") or "Unknown framework"),
        slug=_text(item.get("slug")),
        organization_name=_text(item.get("organization_name")),
        description=_text(item.get("description")),
        when_to_use=_text(item.get("when_to_use")),
        platforms=[str(platform) for platform in item.get("platforms") or []],
        domain_slug=_text(item.get("domain_slug")),
        domain_name=_text(item.get("domain_name")),
        category_slug=_text(item.get("category_slug")),
        category_name=_text(item.get("category_name")),
        github_url=_text(item.get("github_url")),
        docs_url=_text(item.get("docs_url")),
        introducing_paper_arxiv_id=_text(item.get("introducing_paper_arxiv_id")),
        paper_count=_int(item.get("paper_count")) or 0,
    )


def evaluation(item: dict[str, Any]) -> Evaluation:
    return Evaluation(
        id=str(item.get("id") or ""),
        model_name=str(item.get("model_name") or "Unknown model"),
        harness=_text(item.get("harness")),
        metrics={name: _metric_value(value) for name, value in _metrics(item).items()},
        best_metric=_text(item.get("best_metric")),
        best_rank=_int(item.get("best_rank")),
        task=_text(item.get("task_name")),
        paper_id=_text(item.get("paper_id")),
        paper_title=_text(item.get("paper_title")),
        paper_arxiv_id=_text(item.get("paper_arxiv_id")),
        paper_published=_text(item.get("paper_published_date")),
        rank_scopes=_rank_scopes(item),
        is_open=(
            item.get("is_open") if isinstance(item.get("is_open"), bool) else None
        ),
        num_parameters=_int(item.get("num_parameters")),
        split=_text(
            item.get("split") or item.get("split_name") or item.get("evaluated_on")
        ),
        shots=_shot_count(item.get("methodology")),
        evaluation_protocol=_text(item.get("methodology")),
        source_url=_absolute_url(
            item.get("result_url")
            or item.get("source_url")
            or item.get("external_source_url")
        ),
        code_url=_absolute_url(item.get("code_url")),
        hf_model_url=_absolute_url(item.get("hf_model_url")),
        updated_at=_text(item.get("updated_at")),
        uses_additional_data=(
            item.get("uses_additional_data")
            if isinstance(item.get("uses_additional_data"), bool)
            else None
        ),
    )


def _rank_scopes(item: dict[str, Any]) -> list[EvaluationRankScope]:
    existing = item.get("rank_scopes")
    if isinstance(existing, list):
        return [
            EvaluationRankScope.model_validate(scope)
            for scope in existing
            if isinstance(scope, dict)
        ]
    if not any(
        item.get(field) is not None for field in ("task_id", "task_name", "best_rank")
    ):
        return []
    return [
        EvaluationRankScope(
            task_id=_text(item.get("task_id")),
            task_name=_text(item.get("task_name")),
            task_slug=_text(item.get("task_slug")),
            rank=_int(item.get("best_rank")),
        )
    ]


def _shot_count(methodology: object) -> int | None:
    match = re.search(r"\b(\d+)\s*[- ]?shot\b", str(methodology), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _metric_value(value: Any) -> float | int | str | None:
    if value is None or isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return str(value)


def metric_directions(
    evaluations: list[Evaluation],
) -> dict[str, Literal["higher", "lower", "unknown"]]:
    lower = {
        "error",
        "loss",
        "perplexity",
        "latency",
        "runtime",
        "wer",
        "cer",
        "eer",
        "fid",
        "mae",
        "rmse",
    }
    higher = {
        "accuracy",
        "precision",
        "recall",
        "f1",
        "bleu",
        "rouge",
        "map",
        "auc",
        "score",
        "em",
    }
    result: dict[str, Literal["higher", "lower", "unknown"]] = {}
    for name in sorted({key for row in evaluations for key in row.metrics}):
        words = set(re.sub(r"[^a-z0-9]+", " ", name.casefold()).split())
        if words & lower:
            result[name] = "lower"
        elif words & higher:
            result[name] = "higher"
        else:
            result[name] = "unknown"
    return result


def public_paper_detail(item: dict[str, Any], *, repo_limit: int = 5) -> PaperDetail:
    """Project public metadata for a paper outside the catalog.

    Linked repositories come from Hugging Face Papers or the abstract, not
    catalog curation, so none is marked official.
    """
    repositories = [
        RepositoryReference(url=url, is_official=False)
        for url in _urls(item.get("repositories"))
    ][:repo_limit]
    return PaperDetail(
        id=str(item.get("id") or ""),
        arxiv_id=_text(item.get("arxiv_id")),
        title=str(item.get("title") or "Untitled paper"),
        abstract=_text(item.get("abstract")),
        authors=[str(author) for author in item.get("authors") or []],
        published=_text(item.get("published")),
        url=_text(item.get("url")),
        pdf_url=_text(item.get("pdf_url")),
        code_repository_count=len(repositories),
        tasks=[],
        methods=[],
        repositories=repositories,
        project_pages=_urls(item.get("project_pages"))[:repo_limit],
    )


def available_at(item: dict[str, Any]) -> list[PaperLink]:
    return [
        PaperLink(label=str(link["label"]), url=str(link["url"]), kind=_text(link.get("kind")))
        for link in item.get("available_at") or []
        if isinstance(link, dict) and link.get("label") and link.get("url")
    ]
