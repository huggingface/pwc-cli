from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _absolute_url(value: object) -> str | None:
    if not value:
        return None
    url = str(value)
    if url.startswith("/"):
        return f"https://paperswithcode.co{url}"
    return url


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
    has_official_implementation: bool = Field(
        description=(
            "True when the catalog marks at least one linked repository as the "
            "paper's official implementation; this is not an independent code audit."
        )
    )
    code_repository_count: int


class PaperPage(OutputModel):
    schema_version: Literal["v1"] = "v1"
    items: list[PaperSummary]
    next_page: int | None = None


class CatalogReference(OutputModel):
    id: str
    name: str
    slug: str | None = None


class TaxonomyReference(CatalogReference):
    url: str


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
    code_repository_count: int
    tasks: list[CatalogReference]
    methods: list[CatalogReference]
    repositories: list[RepositoryReference]
    project_pages: list[str]
    hf_models: list[str]
    hf_datasets: list[str]


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
    url: str | None = None
    full_name: str | None = None
    description: str | None = None
    split: str | None = None
    hf_url: str | None = None
    paper_count: int


class TaskDetail(OutputModel):
    id: str
    name: str
    slug: str
    url: str
    description: str | None = None
    paper_count: int
    area: AreaReference | None = None
    parents: list[CatalogReference]
    children: list[CatalogReference]
    benchmark_count: int
    benchmarks: list[BenchmarkSummary]


class TaskResult(OutputModel):
    schema_version: Literal["v1"] = "v1"
    task: TaskDetail


class MethodDetail(OutputModel):
    id: str
    name: str
    slug: str
    url: str
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


class TaxonomyPage(OutputModel):
    schema_version: Literal["v1"] = "v1"
    items: list[TaxonomyReference]
    next_page: int | None = None


class BenchmarkPage(OutputModel):
    schema_version: Literal["v1"] = "v1"
    items: list[BenchmarkSummary]
    next_page: int | None = None


class EvaluationRankScope(OutputModel):
    task_id: str | None = None
    task_name: str | None = None
    task_slug: str | None = None
    rank: int | None = None


class Evaluation(OutputModel):
    id: str
    model_name: str
    metrics: dict[str, float | int | str | None]
    best_rank: int | None = Field(
        default=None,
        description="Best rank across the task-specific rank scopes listed in rank_scopes.",
    )
    rank_scopes: list[EvaluationRankScope]
    paper_id: str | None = None
    paper_title: str | None = None
    paper_arxiv_id: str | None = None
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
    harness: str | None = None
    source_url: str | None = None
    code_url: str | None = None
    hf_model_url: str | None = None
    updated_at: str | None = None
    uses_additional_data: bool | None = None


class BenchmarkResult(OutputModel):
    schema_version: Literal["v1"] = "v1"
    benchmark: BenchmarkSummary
    evaluation_count: int
    evaluations: list[Evaluation]
    metric_directions: dict[str, Literal["higher", "lower", "unknown"]]
    page: int
    next_page: int | None = None
    ranking_note: str = "Ranks are scoped by task and are not necessarily comparable across rank_scopes."


class EvaluationPage(OutputModel):
    schema_version: Literal["v1"] = "v1"
    paper: str
    evaluation_count: int
    evaluations: list[Evaluation]
    page: int
    next_page: int | None = None


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
        url=_absolute_url(item.get("url_abs") or item.get("source_url")),
        has_official_implementation=item.get("has_official_implementation") is True,
        code_repository_count=int(item.get("code_repository_count") or 0),
    )


def catalog_reference(item: dict[str, Any]) -> CatalogReference:
    return CatalogReference(
        id=str(item.get("id") or ""),
        name=str(item.get("name") or item.get("slug") or "Unknown"),
        slug=str(item["slug"]) if item.get("slug") else None,
    )


def taxonomy_reference(item: dict[str, Any], *, kind: str) -> TaxonomyReference:
    reference = catalog_reference(item)
    slug = reference.slug or reference.id
    return TaxonomyReference(
        **reference.model_dump(),
        url=f"https://paperswithcode.co/{kind}/{slug}",
    )


def paper_detail(
    item: dict[str, Any], *, include_resources: bool, repo_limit: int
) -> PaperDetail:
    repositories = []
    for repository in item.get("repositories") or []:
        if isinstance(repository, dict) and repository.get("url"):
            repository = RepositoryReference(
                url=str(repository["url"]),
                is_official=repository.get("is_official") is True,
            )
            if include_resources or repository.is_official:
                repositories.append(repository)
    repositories.sort(key=lambda repository: not repository.is_official)
    repositories = repositories[:repo_limit]
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
        url=_absolute_url(item.get("url_abs") or item.get("source_url")),
        pdf_url=_absolute_url(item.get("url_pdf")),
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
        project_pages=project_pages[:repo_limit] if include_resources else [],
        hf_models=[str(value) for value in item.get("hf_models") or []][:repo_limit]
        if include_resources
        else [],
        hf_datasets=[str(value) for value in item.get("hf_datasets") or []][:repo_limit]
        if include_resources
        else [],
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
    slug = str(item["slug"]) if item.get("slug") else None
    return BenchmarkSummary(
        id=str(item.get("id") or ""),
        name=str(item.get("name") or item.get("slug") or "Unknown benchmark"),
        slug=slug,
        url=(
            str(item.get("url_abs"))
            if item.get("url_abs")
            else f"https://paperswithcode.co/dataset/{slug}"
            if slug
            else None
        ),
        full_name=str(item["full_name"]) if item.get("full_name") else None,
        description=str(item["description"]) if item.get("description") else None,
        split=(
            str(item.get("split_name") or item.get("split"))
            if item.get("split_name") or item.get("split")
            else None
        ),
        hf_url=str(item["hf_url"]) if item.get("hf_url") else None,
        paper_count=int(item.get("paper_count") or 0),
    )


def evaluation(item: dict[str, Any]) -> Evaluation:
    metrics = item.get("metrics")
    return Evaluation(
        id=str(item.get("id") or ""),
        model_name=str(item.get("model_name") or "Unknown model"),
        metrics={str(key): _metric_value(value) for key, value in metrics.items()}
        if isinstance(metrics, dict)
        else {},
        best_rank=int(item["best_rank"]) if item.get("best_rank") is not None else None,
        paper_id=str(item["paper_id"]) if item.get("paper_id") else None,
        paper_title=str(item["paper_title"]) if item.get("paper_title") else None,
        paper_arxiv_id=(
            str(item["paper_arxiv_id"]) if item.get("paper_arxiv_id") else None
        ),
        rank_scopes=_rank_scopes(item),
        is_open=(
            item.get("is_open") if isinstance(item.get("is_open"), bool) else None
        ),
        num_parameters=(
            int(item["num_parameters"])
            if isinstance(item.get("num_parameters"), int)
            and not isinstance(item.get("num_parameters"), bool)
            else None
        ),
        split=(
            str(item.get("split") or item.get("split_name") or item.get("evaluated_on"))
            if item.get("split") or item.get("split_name") or item.get("evaluated_on")
            else None
        ),
        shots=_shot_count(item.get("methodology")),
        evaluation_protocol=(
            str(item["methodology"]) if item.get("methodology") else None
        ),
        harness=str(item["harness"]) if item.get("harness") else None,
        source_url=_absolute_url(
            item.get("result_url")
            or item.get("source_url")
            or item.get("external_source_url")
        ),
        code_url=_absolute_url(item.get("code_url")),
        hf_model_url=_absolute_url(item.get("hf_model_url")),
        updated_at=str(item["updated_at"]) if item.get("updated_at") else None,
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
            task_id=str(item["task_id"]) if item.get("task_id") else None,
            task_name=str(item["task_name"]) if item.get("task_name") else None,
            task_slug=str(item["task_slug"]) if item.get("task_slug") else None,
            rank=int(item["best_rank"]) if item.get("best_rank") is not None else None,
        )
    ]


def _shot_count(methodology: object) -> int | None:
    if not methodology:
        return None
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


def merged_evaluations(items: list[dict[str, Any]]) -> list[Evaluation]:
    """Combine equivalent rows while retaining each task-specific rank scope."""
    merged: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for item in items:
        shots = _shot_count(item.get("methodology"))
        base_key = (
            *(
                str(item.get(field) or "")
                for field in ("paper_id", "dataset_id", "model_name", "harness")
            ),
            str(
                item.get("split")
                or item.get("split_name")
                or item.get("evaluated_on")
                or ""
            ),
            str(shots) if shots is not None else "",
        )
        source_metrics = item.get("metrics")
        metrics = {
            str(name): _metric_value(value)
            for name, value in (
                source_metrics.items() if isinstance(source_metrics, dict) else []
            )
        }
        candidates = merged.setdefault(base_key, [])
        current = next(
            (
                candidate
                for candidate in candidates
                if all(
                    name not in candidate["metrics"]
                    or candidate["metrics"][name] == value
                    for name, value in metrics.items()
                )
            ),
            None,
        )
        scope = _rank_scopes(item)
        count = item.get("num_parameters")
        valid_count = (
            count
            if isinstance(count, int) and not isinstance(count, bool) and count > 0
            else None
        )
        if current is None:
            candidates.append(
                {
                    **item,
                    "metrics": metrics,
                    "rank_scopes": [value.model_dump() for value in scope],
                    "_parameter_counts": {valid_count},
                    "_open_values": {
                        item.get("is_open")
                        if isinstance(item.get("is_open"), bool)
                        else None
                    },
                }
            )
            continue
        current["metrics"].update(metrics)
        known_scopes = {
            (value.get("task_id"), value.get("rank"))
            for value in current["rank_scopes"]
        }
        current["rank_scopes"].extend(
            value.model_dump()
            for value in scope
            if (value.task_id, value.rank) not in known_scopes
        )
        ranks = [
            rank
            for rank in (current.get("best_rank"), item.get("best_rank"))
            if isinstance(rank, int)
        ]
        current["best_rank"] = min(ranks) if ranks else None
        current["_parameter_counts"].add(valid_count)
        current["_open_values"].add(
            item.get("is_open") if isinstance(item.get("is_open"), bool) else None
        )
        if len(str(item.get("methodology") or "")) > len(
            str(current.get("methodology") or "")
        ):
            current["methodology"] = item["methodology"]
        if str(item.get("updated_at") or "") > str(current.get("updated_at") or ""):
            current["updated_at"] = item["updated_at"]

    rows = [row for candidates in merged.values() for row in candidates]
    for row in rows:
        counts = row.pop("_parameter_counts")
        row["num_parameters"] = next(iter(counts)) if len(counts) == 1 else None
        openness = row.pop("_open_values")
        row["is_open"] = next(iter(openness)) if len(openness) == 1 else None
    return [
        evaluation(item)
        for item in sorted(
            rows,
            key=lambda item: (
                item.get("best_rank")
                if isinstance(item.get("best_rank"), int)
                else 10**9,
                str(item.get("model_name") or "").casefold(),
            ),
        )
    ]


def metric_directions(
    evaluations: list[Evaluation],
) -> dict[str, Literal["higher", "lower", "unknown"]]:
    names = {name for row in evaluations for name in row.metrics}
    lower_markers = (
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
    )
    higher_markers = (
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
    )
    result: dict[str, Literal["higher", "lower", "unknown"]] = {}
    for name in sorted(names):
        normalized = re.sub(r"[^a-z0-9]+", " ", name.casefold())
        words = set(normalized.split())
        if any(marker in words for marker in lower_markers):
            result[name] = "lower"
        elif any(marker in words for marker in higher_markers):
            result[name] = "higher"
        else:
            result[name] = "unknown"
    return result
