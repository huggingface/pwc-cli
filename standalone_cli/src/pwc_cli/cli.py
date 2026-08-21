"""Explicit read-only parser and deterministic compact output."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pwc_cli import API_CONTRACT_VERSION, __version__
from pwc_cli.skills import SkillInstallError, skills_add
from pwc_cli.transport import Client, ResponseError, TransportError

Handler = Callable[[argparse.Namespace, Client], int]
MAX_METRIC_SCAN_ROWS = 1_000
MODERN_ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$", re.IGNORECASE)
LEGACY_ARXIV_ID = re.compile(r"^[a-z][a-z0-9.-]+/\d{7}(?:v\d+)?$", re.IGNORECASE)


class UsageError(ValueError):
    """Valid syntax whose option values cannot be applied together."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}; run '{self.prog} --help'\n")


def _bounded(value: int, minimum: int, maximum: int, flag: str) -> int:
    if not minimum <= value <= maximum:
        raise argparse.ArgumentTypeError(f"{flag} must be {minimum}-{maximum}")
    return value


def _limit(maximum: int = 100):
    return lambda value: _bounded(int(value), 1, maximum, "--limit")


def _page(value: str) -> int:
    return _bounded(int(value), 1, 100, "--page")


def _page_size(value: str) -> int:
    return _bounded(int(value), 1, 100, "--page-size")


def _benchmarks_per_task(value: str) -> int:
    return _bounded(int(value), 1, 10, "--benchmarks-per-task")


def _method_page_size(value: str) -> int:
    return _bounded(int(value), 1, 500, "--page-size")


def _metric_names(value: str) -> tuple[str, ...]:
    names = tuple(name.strip() for name in value.split(",") if name.strip())
    if not names:
        raise argparse.ArgumentTypeError("metric list must not be empty")
    return names


def _metric_bound(value: str) -> tuple[str, float]:
    name, separator, raw_threshold = value.partition("=")
    if not separator or not name.strip() or not raw_threshold.strip():
        raise argparse.ArgumentTypeError("expected METRIC=VALUE")
    try:
        threshold = float(raw_threshold)
    except ValueError as error:
        raise argparse.ArgumentTypeError("metric threshold must be numeric") from error
    return name.strip(), threshold


def _metric_sort(value: str) -> tuple[str, str]:
    name, separator, direction = value.rpartition(":")
    if not separator:
        return value.strip(), "desc"
    if not name.strip() or direction not in {"asc", "desc"}:
        raise argparse.ArgumentTypeError("expected METRIC[:asc|desc]")
    return name.strip(), direction


def _pareto_objectives(value: str) -> tuple[tuple[str, str], ...]:
    objectives = []
    for item in value.split(","):
        name, separator, direction = item.strip().rpartition(":")
        if not separator or not name.strip() or direction not in {"higher", "lower"}:
            raise argparse.ArgumentTypeError("expected METRIC:higher,METRIC:lower")
        objectives.append((name.strip(), direction))
    if len(objectives) < 2:
        raise argparse.ArgumentTypeError(
            "Pareto selection requires at least two metrics"
        )
    return tuple(objectives)


def _clean(value: object) -> str:
    if value is None:
        return ""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\r", "")
        .replace("\n", "\\n")
    )


def _print_table(
    headers: tuple[str, ...],
    rows: list[tuple[object, ...]],
    *,
    right_align: tuple[int, ...] = (),
    align_when_captured: bool = False,
) -> None:
    rendered = [list(headers)] + [[_clean(value) for value in row] for row in rows]
    if not sys.stdout.isatty() and not align_when_captured:
        for row in rendered:
            print("\t".join(row))
        return

    widths = [max(len(row[index]) for row in rendered) for index in range(len(headers))]
    right = set(right_align)
    for row in rendered:
        cells = []
        for index, value in enumerate(row):
            if index in right:
                cells.append(value.rjust(widths[index]))
            elif index == len(row) - 1:
                cells.append(value)
            else:
                cells.append(value.ljust(widths[index]))
        print("  ".join(cells))


def _rows(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], None
    if not isinstance(payload, dict):
        raise ResponseError("API returned an unexpected response shape")
    values = payload.get("results")
    if values is None:
        values = payload.get("items")
    if not isinstance(values, list):
        raise ResponseError("API response did not contain a result list")
    total = payload.get("count") or payload.get("total")
    return [item for item in values if isinstance(item, dict)], int(
        total
    ) if total is not None else None


def _exact_entity_match(
    reference: str,
    items: list[dict[str, Any]],
    *,
    label: str,
    fields: tuple[str, ...] = ("name", "slug", "id"),
) -> dict[str, Any]:
    target = reference.strip().casefold()
    for field in fields:
        for item in items:
            if str(item.get(field) or "").strip().casefold() == target:
                return item
    suggestions = ", ".join(
        str(item.get("name") or item.get("slug"))
        for item in items[:3]
        if item.get("name") or item.get("slug")
    )
    suffix = f"; closest results: {suggestions}" if suggestions else ""
    raise ResponseError(f"{label} not found: {reference}{suffix}")


def _entity_heading(name: object) -> None:
    value = str(name or "Unnamed")
    print(value if sys.stdout.isatty() else f"# {_markdown_text(value)}")


def _entity_link(label: str, url: str) -> str:
    return f"{label}: {url}" if sys.stdout.isatty() else f"[{label}]({url})"


def _paper_id(item: dict[str, Any]) -> str:
    return _clean(item.get("arxiv_id") or item.get("id"))


def _paper_reference(item: dict[str, Any]) -> str:
    return str(
        item.get("arxiv_id") or item.get("route_identifier") or item.get("id") or ""
    )


def _normalize_title(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _is_paper_identifier(value: str) -> bool:
    return (
        value.isdigit()
        or MODERN_ARXIV_ID.fullmatch(value) is not None
        or LEGACY_ARXIV_ID.fullmatch(value) is not None
    )


def _resolve_paper(reference: str, client: Client) -> str:
    candidate = reference.strip()
    if not candidate:
        raise ResponseError("Paper reference cannot be empty")
    if _is_paper_identifier(candidate):
        return candidate

    payload = client.get(
        "papers/search",
        {
            "q": candidate,
            "page": 1,
            "page_size": 20,
            "mode": "keyword",
        },
    ).json()
    items, _total = _rows(payload)
    target = _normalize_title(candidate)
    exact: dict[str, dict[str, Any]] = {}
    for item in items:
        resolved = _paper_reference(item)
        if resolved and _normalize_title(item.get("title")) == target:
            exact.setdefault(resolved, item)
    if len(exact) == 1:
        return next(iter(exact))

    def describe(item: dict[str, Any]) -> str:
        title = _clean(item.get("title") or "Untitled")
        resolved = _clean(_paper_reference(item) or "unknown ID")
        return f"{title} ({resolved})"

    if exact:
        matches = "; ".join(describe(item) for item in exact.values())
        raise ResponseError(f"Paper title is ambiguous: {_clean(candidate)}; {matches}")

    suggestions = "; ".join(describe(item) for item in items[:3])
    suffix = f"; closest results: {suggestions}" if suggestions else ""
    raise ResponseError(f"Paper title not found: {_clean(candidate)}{suffix}")


def _paper_rows(items: list[dict[str, Any]]) -> None:
    rows = []
    for item in items:
        published = _clean(item.get("published") or item.get("date_published"))
        rows.append(
            (
                _paper_id(item),
                item.get("title"),
                published[:4],
                item.get("citation_count"),
            )
        )
    _print_table(("id", "title", "year", "citations"), rows, right_align=(2, 3))


def _emit_page(
    payload: Any,
    args: argparse.Namespace,
    renderer: Callable[[list[dict[str, Any]]], None],
) -> int:
    if args.json:
        print(
            json.dumps(
                {"schema_version": API_CONTRACT_VERSION, "data": payload},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0
    items, total = _rows(payload)
    renderer(items)
    page = getattr(args, "page", 1)
    page_size = getattr(args, "page_size", getattr(args, "limit", len(items) or 1))
    if total is not None and page * page_size < total:
        print(f"# more: rerun with --page {page + 1}", file=sys.stderr)
    return 0


def _validate_date_range(args: argparse.Namespace) -> None:
    if (
        args.start_date is not None
        and args.end_date is not None
        and args.start_date > args.end_date
    ):
        raise UsageError("start date must be on or before end date")


def search(args: argparse.Namespace, client: Client) -> int:
    _validate_date_range(args)
    payload = client.get(
        "papers/search",
        {
            "q": args.query,
            "page": args.page,
            "page_size": args.limit,
            "mode": args.mode,
            "start_date": args.start_date,
            "end_date": args.end_date,
        },
    ).json()
    return _emit_page(payload, args, _paper_rows)


def paper_info(args: argparse.Namespace, client: Client) -> int:
    paper = _resolve_paper(args.paper, client)
    payload = client.get(
        f"papers/{paper}", {"include_resources": args.include_resources}
    ).json()
    evaluations = None
    if args.include_evals:
        paper_id = payload.get("id")
        if not paper_id:
            raise ResponseError("Paper response did not contain an ID")
        evaluations = _paper_evaluations(client, paper_id)
        payload = {**payload, "evaluations": evaluations}
    if args.json:
        print(
            json.dumps(
                {"schema_version": API_CONTRACT_VERSION, "data": payload},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0
    organizations = ", ".join(
        str(item.get("name") or item.get("slug"))
        for item in payload.get("organizations") or []
        if isinstance(item, dict) and (item.get("name") or item.get("slug"))
    )
    fields = (
        ("id", payload.get("arxiv_id") or payload.get("id")),
        ("title", payload.get("title")),
        ("published", payload.get("published")),
        ("authors", ", ".join(payload.get("authors") or [])),
        ("organizations", organizations),
        ("conference", payload.get("conference_name") or payload.get("conference")),
        ("citations", payload.get("citation_count")),
        ("url", payload.get("url_abs") or payload.get("source_url")),
    )
    for label, value in fields:
        if value not in (None, "", []):
            print(f"{label}: {_clean(value)}")

    abstract = payload.get("abstract")
    if abstract:
        print("\n## Abstract\n")
        print(str(abstract).replace("\r", "").strip())

    if evaluations is not None:
        _render_paper_evaluations(evaluations["results"])

    print("\n## Paper lineage")
    for title, papers in (
        ("Predecessors", payload.get("predecessor_papers") or []),
        ("Successors", payload.get("successor_papers") or []),
    ):
        print(f"\n### {title}\n")
        if papers:
            for related_paper in papers:
                print(_paper_info_lineage_markdown(related_paper))
        else:
            print("- None")

    if args.include_resources:
        for title, field in (
            ("Repositories", "repositories"),
            ("Project pages", "project_pages"),
        ):
            resources = [
                resource
                for resource in payload.get(field) or []
                if (resource.get("url") if isinstance(resource, dict) else resource)
            ]
            resources.sort(
                key=lambda resource: (
                    not (
                        isinstance(resource, dict)
                        and resource.get("is_official") is True
                    )
                )
            )
            if resources:
                print(f"\n## {title}\n")
                for resource in resources:
                    if isinstance(resource, dict):
                        official = (
                            "**Official:** " if resource.get("is_official") else ""
                        )
                        url = resource["url"]
                    else:
                        official = ""
                        url = resource
                    print(f"- {official}{_clean(url)}")

        artifacts = [
            (label, url)
            for label, field in (
                ("Model", "hf_models"),
                ("Dataset", "hf_datasets"),
                ("Space", "hf_spaces"),
            )
            for url in payload.get(field) or []
            if url
        ]
        if artifacts:
            print("\n## Hugging Face artifacts\n")
            for label, url in artifacts:
                print(f"- **{label}:** {_clean(url)}")
    return 0


def _paper_info_lineage_markdown(item: dict[str, Any]) -> str:
    reference = item.get("route_identifier")
    title = (
        str(item.get("title") or reference or "Unknown paper")
        .replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
    published = f" ({item['published']})" if item.get("published") else ""
    if not reference:
        return f"- {title}{published}"
    url = f"https://paperswithcode.co/paper/{quote(str(reference), safe='.')}"
    return f"- [{title}]({url}){published}"


def paper_read(args: argparse.Namespace, client: Client) -> int:
    paper = _resolve_paper(args.paper, client)
    response = client.get(f"research/papers/{paper}/read")
    markdown = response.body.decode("utf-8", errors="replace")
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": API_CONTRACT_VERSION,
                    "data": {"paper": paper, "markdown": markdown},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0
    sys.stdout.write(markdown)
    if markdown and not markdown.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def paper_list(args: argparse.Namespace, client: Client) -> int:
    _validate_date_range(args)
    requested_filters = {
        name: value
        for name, value in {
            "task": args.task,
            "method": args.method,
            "conference": args.conference,
            "framework": args.framework,
            "organization": args.organization,
            "start_date": args.start_date.isoformat()
            if args.start_date is not None
            else None,
            "end_date": args.end_date.isoformat()
            if args.end_date is not None
            else None,
        }.items()
        if value is not None
    }
    payload = client.get(
        "papers/",
        {
            "page": args.page,
            "page_size": args.page_size,
            "search": args.search,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "task": args.task,
            "method": args.method,
            "conference": args.conference,
            "framework": args.framework,
            "organization": args.organization,
            "latest_only": not args.all_versions,
            "order_by": args.order_by,
            "order_dir": args.order_dir,
            "include_resources": args.include_resources,
        },
    ).json()
    if requested_filters:
        applied = payload.get("applied_filters")
        if not isinstance(applied, dict) or any(
            str(applied.get(name, "")).casefold() != str(value).casefold()
            for name, value in requested_filters.items()
        ):
            raise ResponseError(
                "Papers API did not confirm the requested catalog filters; "
                "the server must be upgraded before using these flags"
            )
    return _emit_page(payload, args, _paper_rows)


def paper_recent(args: argparse.Namespace, client: Client) -> int:
    return _emit_page(
        client.get("papers/recent", {"limit": args.limit}).json(), args, _paper_rows
    )


def paper_trending(args: argparse.Namespace, client: Client) -> int:
    return _emit_page(
        client.get(
            "papers/trending",
            {
                "limit": args.limit,
                "max_age_days": args.max_age_days,
                "min_velocity": args.min_velocity,
            },
        ).json(),
        args,
        _paper_rows,
    )


def paper_related(args: argparse.Namespace, client: Client) -> int:
    paper = _resolve_paper(args.paper, client)
    return _emit_page(
        client.get(f"papers/{paper}/related", {"limit": args.limit}).json(),
        args,
        _paper_rows,
    )


def _lineage_markdown(item: dict[str, Any]) -> str:
    reference = item.get("reference")
    title = (
        str(item.get("title") or reference or "Unknown paper")
        .replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
    if not reference:
        return f"- {title}"
    url = f"https://paperswithcode.co/paper/{quote(str(reference), safe='.')}"
    return f"- [{title}]({url}) (`{_clean(reference)}`)"


def paper_lineage(args: argparse.Namespace, client: Client) -> int:
    paper_reference = _resolve_paper(args.paper, client)
    payload = client.get(f"research/papers/{paper_reference}/lineage").json()
    if args.json:
        print(
            json.dumps(
                {"schema_version": API_CONTRACT_VERSION, "data": payload},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0
    paper = payload.get("paper") or {}
    print("# Paper lineage")
    for title, items in (
        ("Paper", [paper] if paper else []),
        ("Predecessors", payload.get("predecessors") or []),
        ("Successors", payload.get("successors") or []),
    ):
        print(f"\n## {title}\n")
        if items:
            for item in items:
                print(_lineage_markdown(item))
        else:
            print("- None")
    return 0


def _area_catalog(client: Client) -> list[dict[str, Any]]:
    payload = client.get(
        "areas/",
        {"page": 1, "page_size": 500, "ordering": "name"},
    ).json()
    areas, _total = _rows(payload)
    return areas


def _resolve_area(
    reference: str | None, client: Client
) -> tuple[str | None, dict[str, str]]:
    areas = _area_catalog(client)
    names = {
        str(item.get("id")): str(item.get("name"))
        for item in areas
        if item.get("id") is not None and item.get("name")
    }
    if reference is None:
        return None, names

    target = reference.strip().casefold()
    for area_id, name in names.items():
        if target in (area_id.casefold(), name.casefold()):
            return area_id, names

    available = ", ".join(sorted(names.values(), key=str.casefold))
    raise ResponseError(f"Area not found: {reference}; available areas: {available}")


def _ordering(field: str, direction: str) -> str:
    return f"-{field}" if direction == "desc" else field


def _task_list_grouped(args: argparse.Namespace, client: Client) -> int:
    if args.page != 1 or args.page_size != 50 or args.level is not None:
        raise ResponseError(
            "--group-by-area cannot be combined with pagination or --level; "
            "use --flat for the complete task endpoint"
        )

    payload = client.get("areas/with-tasks/").json()
    areas, _total = _rows(payload)
    if args.area:
        available_areas = areas
        target = args.area.strip().casefold()
        areas = [
            area
            for area in areas
            if target
            in (
                str(area.get("id") or "").casefold(),
                str(area.get("name") or "").casefold(),
            )
        ]
        if not areas:
            names = ", ".join(
                sorted(
                    (
                        str(area.get("name"))
                        for area in available_areas
                        if area.get("name")
                    ),
                    key=str.casefold,
                )
            )
            raise ResponseError(
                f"Area not found: {args.area}; available areas: {names}"
            )

    reverse = args.order_dir == "desc"

    def sort_key(item: dict[str, Any]):
        value = item.get(args.order_by)
        if isinstance(value, str):
            value = value.casefold()
        return value is None, value

    grouped = []
    for area in areas:
        tasks = [task for task in area.get("tasks") or [] if isinstance(task, dict)]
        tasks.sort(key=sort_key, reverse=reverse)
        grouped.append({**area, "tasks": tasks})
    data = {"results": grouped}
    if args.json:
        print(
            json.dumps(
                {"schema_version": API_CONTRACT_VERSION, "data": data},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0

    for area_index, area in enumerate(grouped):
        if area_index:
            print()
        print(f"# Area: {_clean(area.get('name'))}")
        tasks = area["tasks"]
        if not tasks:
            print("\n- None")
            continue
        for task in tasks:
            name = _clean(task.get("name") or task.get("slug") or "Unnamed task")
            slug = _clean(task.get("slug"))
            counts = []
            for field, singular, plural in (
                ("paper_count", "paper", "papers"),
                ("benchmark_count", "benchmark", "benchmarks"),
                ("evaluation_count", "eval", "evals"),
            ):
                count = task.get(field)
                count_text = f"{int(count):,}" if count is not None else "unknown"
                counts.append(f"{count_text} {singular if count == 1 else plural}")
            slug_text = f" (`{slug}`)" if slug else ""
            print(f"- **{name}**{slug_text} — {' · '.join(counts)}")
    return 0


def task_list(args: argparse.Namespace, client: Client) -> int:
    automatic_grouping = (
        sys.stdout.isatty()
        and not args.json
        and not args.flat
        and args.page == 1
        and args.page_size == 50
        and args.level is None
    )
    if args.group_by_area or automatic_grouping:
        return _task_list_grouped(args, client)

    area_id, area_names = _resolve_area(args.area, client)
    payload = client.get(
        "tasks/",
        {
            "page": args.page,
            "page_size": args.page_size,
            "area_id": area_id,
            "level": args.level,
            "visible_only": args.visible_only,
            "ordering": _ordering(args.order_by, args.order_dir),
        },
    ).json()

    def render(items: list[dict[str, Any]]) -> None:
        _print_table(
            (
                "id",
                "slug",
                "name",
                "area",
                "level",
                "papers",
                "benchmarks",
                "evals",
            ),
            [
                (
                    item.get("id"),
                    item.get("slug"),
                    item.get("name"),
                    area_names.get(str(item.get("area_id")), ""),
                    item.get("level"),
                    item.get("paper_count"),
                    item.get("benchmark_count"),
                    item.get("evaluation_count"),
                )
                for item in items
            ],
            right_align=(4, 5, 6, 7),
        )

    return _emit_page(payload, args, render)


def _task_match(name: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    target = name.strip().casefold()
    for field in ("name", "slug", "id"):
        for item in items:
            if str(item.get(field) or "").strip().casefold() == target:
                return item
    return None


def _task_reference(item: dict[str, Any], *, markdown: bool) -> str:
    name = str(item.get("name") or item.get("slug") or item.get("id") or "Task")
    slug = item.get("slug") or item.get("id")
    url = f"https://paperswithcode.co/tasks/{quote(str(slug), safe='-')}"
    return f"[{_markdown_text(name)}]({url})" if markdown else f"{name} [{slug}]"


def _task_paper_reference(item: dict[str, Any], *, markdown: bool) -> str:
    title = str(item.get("title") or item.get("arxiv_id") or item.get("id") or "Paper")
    reference = item.get("arxiv_id") or item.get("id")
    url = item.get("url_abs") or (
        f"https://paperswithcode.co/paper/{quote(str(reference), safe='.')}"
        if reference
        else None
    )
    if markdown and url:
        return f"[{_markdown_text(title)}]({url})"
    return f"{title} [{reference}]" if reference else title


def _common_methods(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for paper in papers:
        for method in paper.get("methods") or []:
            if not isinstance(method, dict):
                continue
            key = str(method.get("slug") or method.get("id") or "")
            if not key:
                continue
            entry = counts.setdefault(key, {**method, "paper_count": 0})
            entry["paper_count"] += 1
    return sorted(
        counts.values(),
        key=lambda item: (
            -int(item["paper_count"]),
            str(item.get("name") or "").casefold(),
        ),
    )[:8]


def _api_ranked_benchmarks(
    page_benchmarks: object,
    trend_payload: object,
) -> list[dict[str, Any]]:
    if not isinstance(page_benchmarks, list):
        raise ResponseError("task page API did not contain a benchmark list")
    if not isinstance(trend_payload, dict) or not isinstance(
        trend_payload.get("results"), list
    ):
        raise ResponseError(
            "benchmark trends API returned an unexpected response shape"
        )

    benchmarks = [item for item in page_benchmarks if isinstance(item, dict)]
    by_id = {str(item.get("id")): item for item in benchmarks}
    ranked = []
    ranked_ids = set()
    for trend in trend_payload["results"]:
        if not isinstance(trend, dict):
            continue
        benchmark_id = str(trend.get("id"))
        benchmark = by_id.get(benchmark_id)
        if benchmark is None:
            continue
        ranked.append({**benchmark, **trend})
        ranked_ids.add(benchmark_id)
    ranked.extend(
        benchmark
        for benchmark in benchmarks
        if str(benchmark.get("id")) not in ranked_ids
    )
    return ranked


def task_detail(args: argparse.Namespace, client: Client) -> int:
    if not args.name:
        raise ResponseError("task inspection requires --name")
    if args.name.strip().isdigit():
        summary = client.get(f"tasks/{quote(args.name.strip(), safe='')}").json()
    else:
        search_payload = client.get(
            "tasks/",
            {"q": args.name, "page": 1, "page_size": 100},
        ).json()
        candidates, _total = _rows(search_payload)
        summary = _task_match(args.name, candidates)
        if summary is None:
            suggestions = ", ".join(
                str(item.get("name")) for item in candidates[:3] if item.get("name")
            )
            suffix = f"; closest results: {suggestions}" if suggestions else ""
            raise ResponseError(f"Task not found: {args.name}{suffix}")

    task_id = str(summary["id"])
    page = client.get(f"tasks/{quote(task_id, safe='')}/page").json()
    if not isinstance(page, dict) or not isinstance(page.get("task"), dict):
        raise ResponseError("task page API returned an unexpected response shape")
    benchmark_trends = client.get(
        f"tasks/{quote(task_id, safe='')}/trending-benchmarks",
        {"limit": 100, "min_recent_papers": 0},
    ).json()
    benchmarks = _api_ranked_benchmarks(page.get("benchmarks"), benchmark_trends)
    papers_payload = client.get(
        f"tasks/{quote(task_id, safe='')}/papers",
        {
            "page": 1,
            "page_size": 100,
            "order_by": "trending",
            "order_dir": "desc",
            "include_resources": True,
        },
    ).json()
    papers, paper_total = _rows(papers_payload)
    areas = _area_catalog(client)
    area = next(
        (item for item in areas if str(item.get("id")) == str(summary.get("area_id"))),
        None,
    )

    task = {**page["task"]}
    for field in ("paper_count", "benchmark_count", "evaluation_count"):
        if summary.get(field) is not None:
            task[field] = summary[field]
    data = {
        "task": task,
        "area": area,
        "parents": page.get("parents") or [],
        "sisters": page.get("sisters") or [],
        "recommended_frameworks": page.get("recommended_frameworks") or [],
        "children": page.get("children") or [],
        "benchmarks": benchmarks,
        "subtask_benchmarks": page.get("subtask_benchmarks") or [],
        "common_methods": _common_methods(papers),
        "paper_sample_size": len(papers),
        "paper_count": paper_total if paper_total is not None else len(papers),
        "papers": papers[:10],
    }
    if args.json:
        print(
            json.dumps(
                {"schema_version": API_CONTRACT_VERSION, "data": data},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0

    interactive = sys.stdout.isatty()
    markdown = not interactive
    name = str(task.get("name") or task.get("slug") or args.name)
    print(name if interactive else f"# {_markdown_text(name)}")
    metadata = []
    if area and area.get("name"):
        metadata.append(f"Area: {area['name']}")
    if task.get("level") is not None:
        metadata.append(f"Level: {task['level']}")
    for field, label in (
        ("paper_count", "papers"),
        ("benchmark_count", "benchmarks"),
        ("evaluation_count", "evaluations"),
    ):
        if task.get(field) is not None:
            metadata.append(f"{int(task[field]):,} {label}")
    if metadata:
        print(f"\n{' · '.join(metadata)}")
    if task.get("description"):
        description = str(task["description"]).replace("\r", " ").replace("\n", " ")
        print(f"\n{_markdown_text(description) if markdown else description}")
    task_slug = task.get("slug") or task.get("id")
    task_url = f"https://paperswithcode.co/tasks/{quote(str(task_slug), safe='-')}"
    task_link = f"[View task]({task_url})" if markdown else f"View task: {task_url}"
    print(f"\n{task_link}")

    def heading(title: str) -> None:
        print(f"\n{'## ' if markdown else ''}{title}\n")

    parents = data["parents"]
    if parents:
        heading("Task hierarchy")
        chain = list(reversed(parents)) + [task]
        print(" → ".join(_task_reference(item, markdown=markdown) for item in chain))

    trends = task.get("research_trends") or []
    if trends:
        heading("Research trends")
        for trend in trends:
            print(f"- {_markdown_text(trend) if markdown else str(trend)}")

    frameworks = data["recommended_frameworks"]
    if frameworks:
        heading("Recommended frameworks")
        for framework in frameworks:
            framework_name = "/".join(
                part for part in (framework.get("owner"), framework.get("name")) if part
            ) or str(framework.get("url") or framework.get("id"))
            stars = int(framework.get("num_stars") or 0)
            if markdown:
                print(
                    f"- [{_markdown_text(framework_name)}]({framework.get('url')}) — {stars:,} stars"
                )
            else:
                print(f"- {framework_name} — {stars:,} stars — {framework.get('url')}")

    for title, items in (
        ("Sister tasks", data["sisters"]),
        ("Subtasks", data["children"]),
    ):
        if items:
            heading(title)
            for item in items:
                print(f"- {_task_reference(item, markdown=markdown)}")

    methods = data["common_methods"]
    if methods:
        heading(f"Common methods (from {len(papers)} sampled papers)")
        for method in methods:
            method_name = str(
                method.get("name") or method.get("slug") or method.get("id")
            )
            method_slug = method.get("slug") or method.get("id")
            method_url = (
                f"https://paperswithcode.co/methods/{quote(str(method_slug), safe='-')}"
            )
            label = (
                f"[{_markdown_text(method_name)}]({method_url})"
                if markdown
                else f"{method_name} [{method_slug}]"
            )
            print(f"- {label} — {int(method['paper_count']):,} papers")

    benchmarks = data["benchmarks"]
    if benchmarks:
        heading("Benchmarks")
        for benchmark in benchmarks:
            benchmark_name = str(
                benchmark.get("name") or benchmark.get("slug") or benchmark.get("id")
            )
            benchmark_slug = benchmark.get("slug") or benchmark.get("id")
            benchmark_url = f"https://paperswithcode.co/benchmark/{quote(str(benchmark_slug), safe='-')}"
            label = (
                f"[{_markdown_text(benchmark_name)}]({benchmark_url})"
                if markdown
                else f"{benchmark_name} [{benchmark_slug}]"
            )
            details = []
            if benchmark.get("paper_count") is not None:
                details.append(f"{int(benchmark['paper_count']):,} papers")
            if benchmark.get("support_weighted_score") is not None:
                details.append(
                    f"trend score: {float(benchmark['support_weighted_score']):.2f}"
                )
            if benchmark.get("best_model_name"):
                details.append(f"best model: {benchmark['best_model_name']}")
            suffix = f" — {', '.join(details)}" if details else ""
            print(f"- {label}{suffix}")

    subtask_benchmarks = {
        str(item.get("subtask_id")): item.get("benchmarks") or []
        for item in data["subtask_benchmarks"]
        if isinstance(item, dict)
    }
    if any(subtask_benchmarks.values()):
        heading("Benchmarks by subtask")
        for child in data["children"]:
            child_benchmarks = subtask_benchmarks.get(str(child.get("id")), [])
            if not child_benchmarks:
                continue
            print(f"- {_task_reference(child, markdown=markdown)}")
            for benchmark in child_benchmarks:
                benchmark_name = str(
                    benchmark.get("name")
                    or benchmark.get("slug")
                    or benchmark.get("id")
                )
                benchmark_slug = benchmark.get("slug") or benchmark.get("id")
                benchmark_url = (
                    "https://paperswithcode.co/benchmark/"
                    f"{quote(str(benchmark_slug), safe='-')}"
                )
                label = (
                    f"[{_markdown_text(benchmark_name)}]({benchmark_url})"
                    if markdown
                    else f"{benchmark_name} [{benchmark_slug}]"
                )
                print(f"  - {label}")

    top_papers = data["papers"]
    if top_papers:
        heading(f"Trending papers (top {len(top_papers)} of {data['paper_count']})")
        for paper in top_papers:
            details = []
            if paper.get("published"):
                details.append(str(paper["published"]))
            if paper.get("citation_count") is not None:
                details.append(f"{int(paper['citation_count']):,} citations")
            suffix = f" — {', '.join(details)}" if details else ""
            print(f"- {_task_paper_reference(paper, markdown=markdown)}{suffix}")
    return 0


def method_detail(args: argparse.Namespace, client: Client) -> int:
    if not args.name:
        raise ResponseError("method inspection requires --name")
    if args.name.strip().isdigit():
        method = client.get(f"methods/{quote(args.name.strip(), safe='')}").json()
    else:
        payload = client.get(
            "methods/",
            {"q": args.name, "page": 1, "page_size": 100},
        ).json()
        candidates, _total = _rows(payload)
        summary = _exact_entity_match(
            args.name,
            candidates,
            label="Method",
            fields=("name", "full_name", "slug", "id"),
        )
        method_id = quote(str(summary.get("id") or summary.get("slug")), safe="")
        method = client.get(f"methods/{method_id}").json()
    area = next(
        (
            item
            for item in _area_catalog(client)
            if str(item.get("id")) == str(method.get("area_id"))
        ),
        None,
    )
    data = {"method": method, "area": area}
    if args.json:
        print(
            json.dumps(
                {"schema_version": API_CONTRACT_VERSION, "data": data},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0

    _entity_heading(method.get("name") or method.get("slug"))
    metadata = []
    if area and area.get("name"):
        metadata.append(f"Area: {area['name']}")
    if method.get("introduced_year") is not None:
        metadata.append(f"Introduced: {method['introduced_year']}")
    if method.get("paper_count") is not None:
        metadata.append(f"{int(method['paper_count']):,} papers")
    if metadata:
        print(f"\n{' · '.join(metadata)}")
    if method.get("description"):
        print(f"\n{str(method['description']).strip()}")
    source_url = method.get("source_url")
    if source_url:
        if str(source_url).startswith("/"):
            source_url = f"https://paperswithcode.co{source_url}"
        source_label = method.get("source_title") or "Source paper"
        print(f"\n{_entity_link(str(source_label), str(source_url))}")
    slug = method.get("slug") or method.get("id")
    url = f"https://paperswithcode.co/methods/{quote(str(slug), safe='-')}"
    print(f"\n{_entity_link('View method', url)}")
    return 0


def method_list(args: argparse.Namespace, client: Client) -> int:
    area_id, area_names = _resolve_area(args.area, client)
    payload = client.get(
        "methods/",
        {
            "page": args.page,
            "page_size": args.page_size,
            "area_id": area_id,
            "introduced_year": args.introduced_year,
            "ordering": _ordering(args.order_by, args.order_dir),
        },
    ).json()

    def render(items: list[dict[str, Any]]) -> None:
        _print_table(
            ("id", "slug", "name", "full_name", "area", "introduced", "papers"),
            [
                (
                    item.get("id"),
                    item.get("slug"),
                    item.get("name"),
                    item.get("full_name"),
                    area_names.get(str(item.get("area_id")), ""),
                    item.get("introduced_year"),
                    item.get("paper_count"),
                )
                for item in items
            ],
            right_align=(5, 6),
        )

    return _emit_page(payload, args, render)


def conference_detail(args: argparse.Namespace, client: Client) -> int:
    if not args.name:
        raise ResponseError("conference inspection requires --name")
    payload = client.get("conferences/").json()
    conferences, _total = _rows(payload)
    summary = _exact_entity_match(args.name, conferences, label="Conference")
    slug = str(summary.get("slug") or summary.get("id"))
    conference = client.get(f"conferences/{quote(slug, safe='')}").json()
    if args.json:
        print(
            json.dumps(
                {"schema_version": API_CONTRACT_VERSION, "data": conference},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0

    _entity_heading(conference.get("name") or conference.get("slug"))
    metadata = []
    if conference.get("year") is not None:
        metadata.append(str(conference["year"]))
    if conference.get("paper_count") is not None:
        metadata.append(f"{int(conference['paper_count']):,} papers")
    if conference.get("tier"):
        metadata.append(f"Tier: {str(conference['tier']).upper()}")
    if metadata:
        print(f"\n{' · '.join(metadata)}")
    dates = " – ".join(
        str(value)
        for value in (conference.get("start_date"), conference.get("end_date"))
        if value
    )
    venue = ", ".join(
        str(value)
        for value in (conference.get("venue"), conference.get("location"))
        if value
    )
    if dates:
        print(f"\nDates: {dates}")
    if venue:
        print(f"Venue: {venue}")
    if conference.get("description"):
        print(f"\n{str(conference['description']).strip()}")
    for label, field in (("Conference website", "url"), ("Hugging Face", "hf_url")):
        if conference.get(field):
            print(f"\n{_entity_link(label, str(conference[field]))}")
    url = f"https://paperswithcode.co/conferences/{quote(slug, safe='-')}"
    print(f"\n{_entity_link('View conference', url)}")
    return 0


def conference_list(args: argparse.Namespace, client: Client) -> int:
    payload = client.get("conferences/").json()
    items, _total = _rows(payload)
    if args.year is not None:
        items = [item for item in items if item.get("year") == args.year]
    filtered = {"count": len(items), "results": items}

    def render(rows: list[dict[str, Any]]) -> None:
        _print_table(
            ("slug", "name", "year", "papers", "location"),
            [
                (
                    item.get("slug"),
                    item.get("name"),
                    item.get("year"),
                    item.get("paper_count"),
                    item.get("location"),
                )
                for item in rows
            ],
            right_align=(2, 3),
        )

    return _emit_page(filtered, args, render)


def organization_detail(args: argparse.Namespace, client: Client) -> int:
    if not args.name:
        raise ResponseError("organization inspection requires --name")
    payload = client.get("organizations/", {"featured_only": False}).json()
    organizations, _total = _rows(payload)
    summary = _exact_entity_match(args.name, organizations, label="Organization")
    slug = str(summary.get("slug") or summary.get("id"))
    organization = client.get(f"organizations/{quote(slug, safe='')}").json()
    if args.json:
        print(
            json.dumps(
                {"schema_version": API_CONTRACT_VERSION, "data": organization},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0

    _entity_heading(organization.get("name") or organization.get("slug"))
    metadata = []
    if organization.get("paper_count") is not None:
        metadata.append(f"{int(organization['paper_count']):,} papers")
    if organization.get("trending_rank") is not None:
        metadata.append(f"Trending rank: {int(organization['trending_rank']):,}")
    if organization.get("is_featured"):
        metadata.append("Featured")
    if metadata:
        print(f"\n{' · '.join(metadata)}")
    years = [
        value
        for value in (
            organization.get("first_paper_year"),
            organization.get("latest_paper_year"),
        )
        if value is not None
    ]
    if years:
        print(f"\nPaper years: {'–'.join(str(value) for value in years)}")
    if organization.get("description"):
        print(f"\n{str(organization['description']).strip()}")
    for label, field in (
        ("Website", "website_url"),
        ("GitHub", "github_url"),
        ("Hugging Face", "hf_url"),
    ):
        if organization.get(field):
            print(f"\n{_entity_link(label, str(organization[field]))}")
    url = f"https://paperswithcode.co/orgs/{quote(slug, safe='-')}"
    print(f"\n{_entity_link('View organization', url)}")
    return 0


def organization_list(args: argparse.Namespace, client: Client) -> int:
    payload = client.get("organizations/", {"featured_only": args.featured_only}).json()

    def render(items: list[dict[str, Any]]) -> None:
        _print_table(
            ("id", "slug", "name", "papers", "featured", "trend_rank"),
            [
                (
                    item.get("id"),
                    item.get("slug"),
                    item.get("name"),
                    item.get("paper_count"),
                    "yes" if item.get("is_featured") else "no",
                    item.get("trending_rank"),
                )
                for item in items
            ],
            right_align=(3, 5),
        )

    return _emit_page(payload, args, render)


def _framework_catalog_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("domains"), list):
        raise ResponseError("framework catalog returned an unexpected response shape")
    items = []
    for domain in payload["domains"]:
        if not isinstance(domain, dict):
            continue
        for category in domain.get("categories") or []:
            if not isinstance(category, dict):
                continue
            for framework in category.get("frameworks") or []:
                if isinstance(framework, dict):
                    items.append(
                        {
                            **framework,
                            "domain_slug": domain.get("slug"),
                            "domain_name": domain.get("name"),
                            "category_slug": category.get("slug"),
                            "category_name": category.get("name"),
                        }
                    )
    return items


def framework_detail(args: argparse.Namespace, client: Client) -> int:
    if not args.name:
        raise ResponseError("framework inspection requires --name")
    items = _framework_catalog_items(client.get("frameworks/").json())
    framework = _exact_entity_match(args.name, items, label="Framework")
    if args.json:
        print(
            json.dumps(
                {"schema_version": API_CONTRACT_VERSION, "data": framework},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0

    _entity_heading(framework.get("name") or framework.get("slug"))
    metadata = [
        value
        for value in (
            framework.get("domain_name"),
            framework.get("category_name"),
            framework.get("organization_name"),
        )
        if value
    ]
    if framework.get("paper_count") is not None:
        metadata.append(f"{int(framework['paper_count']):,} papers")
    if metadata:
        print(f"\n{' · '.join(str(value) for value in metadata)}")
    if framework.get("description"):
        print(f"\n{str(framework['description']).strip()}")
    if framework.get("when_to_use"):
        print(f"\nWhen to use: {str(framework['when_to_use']).strip()}")
    platforms = framework.get("platforms") or []
    if platforms:
        print(f"\nPlatforms: {', '.join(str(value) for value in platforms)}")
    for label, field in (("GitHub", "github_url"), ("Documentation", "docs_url")):
        if framework.get(field):
            print(f"\n{_entity_link(label, str(framework[field]))}")
    paper = framework.get("introducing_paper_arxiv_id")
    if paper:
        url = f"https://paperswithcode.co/paper/{quote(str(paper), safe='.')}"
        print(f"\n{_entity_link(f'Introducing paper {paper}', url)}")
    slug = framework.get("slug") or framework.get("id")
    url = f"https://paperswithcode.co/frameworks/{quote(str(slug), safe='-')}"
    print(f"\n{_entity_link('View framework', url)}")
    return 0


def framework_list(args: argparse.Namespace, client: Client) -> int:
    items = _framework_catalog_items(client.get("frameworks/").json())
    for field, value in (
        ("domain_slug", args.domain),
        ("category_slug", args.category),
    ):
        if value:
            target = value.strip().casefold()
            name_field = field.replace("slug", "name")
            items = [
                item
                for item in items
                if target
                in {
                    str(item.get(field) or "").casefold(),
                    str(item.get(name_field) or "").casefold(),
                }
            ]
    if args.platform:
        target = args.platform.strip().casefold()
        items = [
            item
            for item in items
            if target
            in {str(value).casefold() for value in item.get("platforms") or []}
        ]
    payload = {"count": len(items), "results": items}

    def render(rows: list[dict[str, Any]]) -> None:
        _print_table(
            ("id", "slug", "name", "domain", "category", "platforms", "papers"),
            [
                (
                    item.get("id"),
                    item.get("slug"),
                    item.get("name"),
                    item.get("domain_name"),
                    item.get("category_name"),
                    ",".join(str(value) for value in item.get("platforms") or []),
                    item.get("paper_count"),
                )
                for item in rows
            ],
            right_align=(6,),
        )

    return _emit_page(payload, args, render)


def benchmark_list(args: argparse.Namespace, client: Client) -> int:
    flat_filters = (
        args.task
        or args.search
        or args.include_descendants
        or args.is_open is not None
        or args.order_by is not None
        or args.order_dir != "asc"
        or args.page != 1
        or args.page_size != 50
    )
    automatic_grouping = (
        sys.stdout.isatty()
        and not args.json
        and not args.flat
        and not args.task
        and not args.search
        and not args.include_descendants
        and args.is_open is None
        and args.order_by is None
        and args.order_dir == "asc"
        and args.page == 1
        and args.page_size == 50
    )
    grouped = (
        args.group_by_area or bool(args.area) or automatic_grouping
    ) and not flat_filters
    if args.area and flat_filters:
        raise UsageError(
            "--area cannot be combined with task/search/pagination filters"
        )
    if args.group_by_area and flat_filters and not args.json:
        print(
            "# filters select task-scoped flat output; --group-by-area ignored",
            file=sys.stderr,
        )
    if grouped:
        return _benchmark_list_grouped(args, client)

    if args.task and args.order_by in (None, "trending"):
        trend_payload = client.get(
            f"tasks/{quote(args.task, safe='')}/trending-benchmarks",
            {
                "limit": 100,
                "min_recent_papers": 0,
                "include_descendants": args.include_descendants,
                "is_open": args.is_open,
            },
        ).json()
        if not isinstance(trend_payload, dict) or not isinstance(
            trend_payload.get("results"), list
        ):
            raise ResponseError("API response did not contain benchmark trends")
        items = [
            item
            for item in trend_payload["results"]
            if isinstance(item, dict)
            and (
                not args.search
                or args.search.casefold()
                in " ".join(
                    str(item.get(field) or "")
                    for field in ("name", "full_name", "slug")
                ).casefold()
            )
            and (
                args.min_eval_count is None
                or int(item.get("all_time_paper_count") or 0) >= args.min_eval_count
            )
        ]
        start = (args.page - 1) * args.page_size
        payload = {
            **trend_payload,
            "count": len(items),
            "results": items[start : start + args.page_size],
        }

        def render_trends(rows: list[dict[str, Any]]) -> None:
            _print_table(
                (
                    "id",
                    "slug",
                    "name",
                    "recent_papers",
                    "ranking_score",
                    "best_model",
                    "paper_title",
                    "code",
                ),
                [
                    (
                        item.get("id"),
                        item.get("slug"),
                        item.get("name"),
                        item.get("recent_paper_count"),
                        item.get("support_weighted_score", item.get("trend_score")),
                        item.get("best_model_name"),
                        item.get("best_paper_title"),
                        item.get("best_code_url"),
                    )
                    for item in rows
                ],
                right_align=(0, 3, 4),
                align_when_captured=True,
            )

        return _emit_page(payload, args, render_trends)

    if args.order_by == "trending":
        raise ResponseError("--order-by trending requires --task")
    ordering = f"-{args.order_by}" if args.order_dir == "desc" else args.order_by
    payload = client.get(
        "datasets/",
        {
            "page": args.page,
            "page_size": args.page_size,
            "q": args.search,
            "task": args.task,
            "include_descendants": args.include_descendants,
            "min_eval_count": args.min_eval_count,
            "is_open": args.is_open,
            "ordering": ordering or "name",
        },
    ).json()

    def render(items: list[dict[str, Any]]) -> None:
        _print_table(
            ("id", "name", "full_name", "evals"),
            [
                (
                    item.get("id"),
                    item.get("name"),
                    item.get("full_name"),
                    item.get("paper_count"),
                )
                for item in items
            ],
            right_align=(0, 3),
            align_when_captured=True,
        )

    return _emit_page(payload, args, render)


def _benchmark_list_grouped(args: argparse.Namespace, client: Client) -> int:
    payload = client.get(
        "areas/with-task-benchmarks/",
        {
            "benchmarks_per_task": args.benchmarks_per_task,
            "min_eval_count": (
                args.min_eval_count if args.min_eval_count is not None else 2
            ),
        },
    ).json()
    areas, _total = _rows(payload)
    if args.area:
        target = args.area.strip().casefold()
        available = areas
        areas = [
            area
            for area in areas
            if target
            in (
                str(area.get("id") or "").casefold(),
                str(area.get("name") or "").casefold(),
            )
        ]
        if not areas:
            names = ", ".join(
                sorted(
                    (str(area.get("name")) for area in available if area.get("name")),
                    key=str.casefold,
                )
            )
            raise ResponseError(
                f"Area not found: {args.area}; available areas: {names}"
            )

    grouped = {"results": areas}
    if args.json:
        print(
            json.dumps(
                {"schema_version": API_CONTRACT_VERSION, "data": grouped},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0

    for area_index, area in enumerate(areas):
        if area_index:
            print()
        print(f"# Area: {_clean(area.get('name') or 'Uncategorized')}")
        print("\nInspect a benchmark with `pwc benchmark --name SLUG`.")
        for task in area.get("tasks") or []:
            task_name = _clean(task.get("name") or task.get("slug") or "Unnamed task")
            task_slug = _clean(task.get("slug"))
            slug_text = f" (`{task_slug}`)" if task_slug else ""
            print(f"\n## {task_name}{slug_text}\n")
            for benchmark in task.get("benchmarks") or []:
                name = _clean(
                    benchmark.get("name") or benchmark.get("slug") or "Benchmark"
                )
                full_name = _clean(benchmark.get("full_name"))
                slug = _clean(benchmark.get("slug"))
                benchmark_id = _clean(benchmark.get("id"))
                identifiers = []
                if full_name and full_name != name:
                    identifiers.append(f"full name: {full_name}")
                if slug:
                    identifiers.append(f"slug: `{slug}`")
                identifiers.append(f"ID: `{benchmark_id}`")
                count = int(benchmark.get("evaluation_count") or 0)
                noun = "evaluation" if count == 1 else "evaluations"
                details = "; ".join(identifiers)
                print(f"- {name} — {details}; {count:,} {noun}")
    return 0


def _benchmark_match(name: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    target = name.strip().casefold()
    for field in ("name", "full_name", "slug", "id"):
        for item in items:
            if str(item.get(field) or "").strip().casefold() == target:
                return item
    return None


def _merged_evaluations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    for item in items:
        key = tuple(
            str(item.get(field) or "")
            for field in (
                "paper_id",
                "task_id",
                "dataset_id",
                "model_name",
                "harness",
            )
        )
        existing = merged.get(key)
        if existing is None:
            existing = {**item, "metrics": dict(item.get("metrics") or {})}
            merged[key] = existing
            continue
        existing["metrics"].update(item.get("metrics") or {})
        ranks = [
            rank
            for rank in (existing.get("best_rank"), item.get("best_rank"))
            if isinstance(rank, int)
        ]
        existing["best_rank"] = min(ranks) if ranks else None
        if not existing.get("best_metric") and item.get("best_metric"):
            existing["best_metric"] = item["best_metric"]
    return sorted(
        merged.values(),
        key=lambda item: (
            item.get("best_rank")
            if isinstance(item.get("best_rank"), int)
            else sys.maxsize,
            str(item.get("model_name") or "").casefold(),
            str(item.get("paper_id") or ""),
        ),
    )


def _markdown_text(value: object) -> str:
    return (
        str(value or "—")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _metric_summary(item: dict[str, Any]) -> str:
    metrics = item.get("metrics") or {}
    if not isinstance(metrics, dict) or not metrics:
        return "—"
    best_metric = item.get("best_metric")
    names = sorted(
        metrics,
        key=lambda name: (
            0 if name == best_metric else 1,
            str(name).casefold(),
        ),
    )
    return ", ".join(f"{name}: {metrics[name]}" for name in names)


def _paper_evaluations(client: Client, paper_id: object) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    total: int | None = None
    page = 1
    while True:
        payload = client.get(
            "evaluations/",
            {
                "page": page,
                "page_size": 100,
                "paper_id": paper_id,
                "ordering": "-benchmark_popularity",
            },
        ).json()
        items, page_total = _rows(payload)
        results.extend(items)
        if total is None:
            total = page_total
        if not items or total is None or len(results) >= total:
            break
        page += 1
    return {"count": total if total is not None else len(results), "results": results}


def _evaluation_source_markdown(item: dict[str, Any]) -> str:
    url = item.get("result_url") or item.get("source_url")
    if not url:
        return "—"
    return f"[source](<{str(url).replace('>', '%3E')}>)"


def _render_paper_evaluations(items: list[dict[str, Any]]) -> None:
    print("\n## Evaluations")
    if not items:
        print("\nNo evaluations found.")
        return
    print("\n| Benchmark | Model | Scores | Rank | Task | Open | Source |")
    print("| --- | --- | --- | ---: | --- | :---: | --- |")
    for item in items:
        model = item.get("model_name") or "—"
        if item.get("harness"):
            model = f"{model} ({item['harness']})"
        print(
            "| "
            + " | ".join(
                (
                    _markdown_text(item.get("dataset_name")),
                    _markdown_text(model),
                    _markdown_text(_metric_summary(item)),
                    _markdown_text(item.get("best_rank")),
                    _markdown_text(item.get("task_name")),
                    "yes" if item.get("is_open", True) else "no",
                    _evaluation_source_markdown(item),
                )
            )
            + " |"
        )


def _metric_value(item: dict[str, Any], requested: str) -> float | None:
    metrics = item.get("metrics") or {}
    if not isinstance(metrics, dict):
        return None
    target = requested.casefold()
    value = next(
        (value for name, value in metrics.items() if str(name).casefold() == target),
        None,
    )
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    normalized = value.strip().replace(",", "")
    if normalized.endswith("%"):
        normalized = normalized[:-1].strip()
    try:
        return float(normalized)
    except ValueError:
        return None


def _available_metrics(items: list[dict[str, Any]]) -> dict[str, str]:
    available: dict[str, str] = {}
    for item in items:
        metrics = item.get("metrics") or {}
        if not isinstance(metrics, dict):
            continue
        for name in metrics:
            available.setdefault(str(name).casefold(), str(name))
    return available


def _metric_requests(args: argparse.Namespace) -> tuple[str, ...]:
    requested = list(args.require_metrics)
    requested.extend(name for name, _threshold in args.minimum_metrics)
    requested.extend(name for name, _threshold in args.maximum_metrics)
    if args.sort_metric:
        requested.append(args.sort_metric[0])
    if args.pareto:
        requested.extend(name for name, _direction in args.pareto)
    return tuple(dict.fromkeys(name.casefold() for name in requested))


def _select_metric_rows(
    items: list[dict[str, Any]], args: argparse.Namespace
) -> list[dict[str, Any]]:
    requested = _metric_requests(args)
    if not requested:
        return items
    available = _available_metrics(items)
    unknown = [name for name in requested if name not in available]
    if unknown:
        choices = ", ".join(sorted(available.values(), key=str.casefold)) or "none"
        raise UsageError(
            f"unknown metric(s): {', '.join(unknown)}; available metrics: {choices}"
        )

    required = {name.casefold() for name in args.require_metrics}
    required.update(name.casefold() for name, _threshold in args.minimum_metrics)
    required.update(name.casefold() for name, _threshold in args.maximum_metrics)
    if args.sort_metric:
        required.add(args.sort_metric[0].casefold())
    if args.pareto:
        required.update(name.casefold() for name, _direction in args.pareto)

    selected = [
        item
        for item in items
        if all(_metric_value(item, name) is not None for name in required)
        and all(
            (_metric_value(item, name) or 0) >= threshold
            for name, threshold in args.minimum_metrics
        )
        and all(
            (_metric_value(item, name) or 0) <= threshold
            for name, threshold in args.maximum_metrics
        )
    ]
    if args.pareto:
        frontier = []
        for candidate in selected:
            dominated = False
            for other in selected:
                if other is candidate:
                    continue
                comparisons = []
                for name, direction in args.pareto:
                    candidate_value = _metric_value(candidate, name)
                    other_value = _metric_value(other, name)
                    assert candidate_value is not None and other_value is not None
                    comparisons.append(
                        other_value >= candidate_value
                        if direction == "higher"
                        else other_value <= candidate_value
                    )
                strictly_better = any(
                    (_metric_value(other, name) or 0)
                    > (_metric_value(candidate, name) or 0)
                    if direction == "higher"
                    else (_metric_value(other, name) or 0)
                    < (_metric_value(candidate, name) or 0)
                    for name, direction in args.pareto
                )
                if all(comparisons) and strictly_better:
                    dominated = True
                    break
            if not dominated:
                frontier.append(candidate)
        selected = frontier
    if args.sort_metric:
        name, direction = args.sort_metric
        selected.sort(
            key=lambda item: _metric_value(item, name) or 0,
            reverse=direction == "desc",
        )
    elif args.pareto:
        name, direction = args.pareto[0]
        selected.sort(
            key=lambda item: _metric_value(item, name) or 0,
            reverse=direction == "higher",
        )
    return selected


def _benchmark_evaluations(
    client: Client,
    benchmark_id: object,
    *,
    is_open: str | None,
    scan_all: bool,
) -> tuple[list[dict[str, Any]], int]:
    evaluations = []
    page = 1
    total = 0
    while True:
        payload = client.get(
            f"datasets/{quote(str(benchmark_id), safe='')}/evaluations/",
            {
                "page": page,
                "page_size": 100,
                "ordering": "best_rank",
                "is_open": is_open,
            },
        ).json()
        current, count = _rows(payload)
        evaluations.extend(current)
        total = count if count is not None else len(evaluations)
        if not scan_all or len(evaluations) >= total:
            return evaluations, total
        if len(evaluations) >= MAX_METRIC_SCAN_ROWS:
            raise ResponseError(
                f"metric selection requires scanning {total} evaluation rows; "
                f"safety limit is {MAX_METRIC_SCAN_ROWS}"
            )
        page += 1


def _paper_markdown(item: dict[str, Any]) -> str:
    title = _markdown_text(
        item.get("paper_title")
        or item.get("paper_arxiv_id")
        or f"Paper {item.get('paper_id')}"
    )
    reference = item.get("paper_arxiv_id") or item.get("paper_id")
    if not reference:
        return title
    url = f"https://paperswithcode.co/paper/{quote(str(reference), safe='.')}"
    return f"[{title}]({url})"


def _paper_terminal(item: dict[str, Any]) -> str:
    """Render a compact paper reference for an aligned terminal table."""
    title = (
        str(
            item.get("paper_title")
            or item.get("paper_arxiv_id")
            or f"Paper {item.get('paper_id')}"
        )
        .replace("\r", " ")
        .replace("\n", " ")
    )
    reference = item.get("paper_arxiv_id") or item.get("paper_id")
    return f"{title} [{reference}]" if reference else title


def benchmark_detail(args: argparse.Namespace, client: Client) -> int:
    if not args.name:
        raise ResponseError("benchmark inspection requires --name")
    search_payload = client.get(
        "datasets/",
        {"q": args.name, "page": 1, "page_size": 100},
    ).json()
    candidates, _total = _rows(search_payload)
    benchmark = _benchmark_match(args.name, candidates)
    if benchmark is None:
        suggestions = ", ".join(
            str(item.get("name")) for item in candidates[:3] if item.get("name")
        )
        suffix = f"; closest results: {suggestions}" if suggestions else ""
        raise ResponseError(f"Benchmark not found: {args.name}{suffix}")

    scan_all = bool(_metric_requests(args))
    evaluations, total = _benchmark_evaluations(
        client, benchmark["id"], is_open=args.is_open, scan_all=scan_all
    )
    merged = _merged_evaluations(evaluations)
    selected = _select_metric_rows(merged, args)
    rows = selected[: args.limit]
    data = {
        "benchmark": benchmark,
        "count": total,
        "matched_count": len(selected),
        "results": rows,
    }
    if args.json:
        print(
            json.dumps(
                {"schema_version": API_CONTRACT_VERSION, "data": data},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0

    interactive = sys.stdout.isatty()
    title = (
        _clean(benchmark.get("name"))
        if interactive
        else _markdown_text(benchmark.get("name"))
    )
    full_name = benchmark.get("full_name")
    print(title if interactive else f"# {title}")
    if full_name and str(full_name).casefold() != str(benchmark.get("name")).casefold():
        print(f"\n{_clean(full_name) if interactive else _markdown_text(full_name)}")
    if benchmark.get("description"):
        description = (
            _clean(benchmark["description"])
            if interactive
            else _markdown_text(benchmark["description"])
        )
        print(f"\n{description}")
    slug = benchmark.get("slug") or benchmark.get("id")
    benchmark_url = f"https://paperswithcode.co/benchmark/{quote(str(slug), safe='-')}"
    link = (
        f"View benchmark: {benchmark_url}"
        if interactive
        else (f"[View benchmark]({benchmark_url})")
    )
    print(f"\n{link}")
    if scan_all:
        print(
            f"\nShowing {len(rows)} of {len(selected)} matching merged rows "
            f"from {data['count']} stored evaluation rows.\n"
        )
    else:
        print(f"\nTop {len(rows)} of {data['count']} evaluation rows.\n")

    if interactive:
        table_rows = []
        for item in rows:
            model = item.get("model_name") or "—"
            if item.get("harness"):
                model = f"{model} ({item['harness']})"
            table_rows.append(
                (
                    item.get("best_rank") or "—",
                    model,
                    _metric_summary(item),
                    item.get("task_name") or "—",
                    _paper_terminal(item),
                    item.get("paper_published_date") or "—",
                    "yes" if item.get("is_open", True) else "no",
                )
            )
        _print_table(
            ("Rank", "Model", "Scores", "Task", "Paper", "Published", "Open"),
            table_rows,
            right_align=(0,),
        )
        return 0

    print("| Rank | Model | Scores | Task | Paper | Published | Open |")
    print("| ---: | --- | --- | --- | --- | --- | :---: |")
    for item in rows:
        model = item.get("model_name") or "—"
        if item.get("harness"):
            model = f"{model} ({item['harness']})"
        print(
            "| "
            + " | ".join(
                (
                    _markdown_text(item.get("best_rank")),
                    _markdown_text(model),
                    _markdown_text(_metric_summary(item)),
                    _markdown_text(item.get("task_name")),
                    _paper_markdown(item),
                    _markdown_text(item.get("paper_published_date")),
                    "yes" if item.get("is_open", True) else "no",
                )
            )
            + " |"
        )
    return 0


def version(_args: argparse.Namespace, _client: Client) -> int:
    print(f"pwc {__version__}\tapi {API_CONTRACT_VERSION}")
    return 0


def _json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json", action="store_true", help="emit stable versioned JSON"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog="pwc",
        description="Read-only Papers With Code research CLI",
        epilog=(
            'Examples:\n  pwc search "small VLMs" --limit 10\n'
            "  pwc paper info 2501.01234\n"
            '  pwc task --name "scene-text-recognition"\n'
            '  pwc organization --name "NVIDIA"\n'
            "  pwc benchmark list --task OCR\n"
            '  pwc benchmark --name "SWE-Bench Pro"'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"pwc {__version__}\tapi {API_CONTRACT_VERSION}",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    search_parser = commands.add_parser(
        "search",
        help="search papers",
        description="Search the paper catalog. Default output is compact TSV.",
    )
    search_parser.add_argument("query", help="title, topic, author, or ArXiv ID")
    search_parser.add_argument(
        "--limit",
        type=_limit(),
        default=10,
        help="results per page, 1-100 (default: 10)",
    )
    search_parser.add_argument(
        "--page", type=_page, default=1, help="page, 1-100 (default: 1)"
    )
    search_parser.add_argument(
        "--mode", choices=("hybrid", "keyword", "semantic"), default="hybrid"
    )
    search_parser.add_argument("--start-date", type=date.fromisoformat)
    search_parser.add_argument("--end-date", type=date.fromisoformat)
    _json(search_parser)
    search_parser.set_defaults(handler=search)

    paper = commands.add_parser("paper", help="inspect papers")
    paper_commands = paper.add_subparsers(dest="paper_command", required=True)
    info = paper_commands.add_parser(
        "info", help="show paper metadata including abstract"
    )
    info.add_argument(
        "paper", help="ArXiv ID, external-paper numeric ID, or exact paper title"
    )
    info.add_argument("--include-resources", action="store_true")
    info.add_argument(
        "--include-evals",
        action="store_true",
        help="include all paper evaluations and render them as Markdown",
    )
    _json(info)
    info.set_defaults(handler=paper_info)
    read = paper_commands.add_parser("read", help="print stored paper Markdown")
    read.add_argument("paper", help="modern ArXiv ID or exact paper title")
    _json(read)
    read.set_defaults(handler=paper_read)
    listing = paper_commands.add_parser("list", help="list and filter papers")
    listing.add_argument("--page", type=_page, default=1)
    listing.add_argument("--page-size", type=_page_size, default=20)
    listing.add_argument("--search")
    listing.add_argument("--start-date", type=date.fromisoformat)
    listing.add_argument("--end-date", type=date.fromisoformat)
    listing.add_argument("--task", help="exact task name, slug, or ID")
    listing.add_argument("--method", help="exact method name, full name, slug, or ID")
    listing.add_argument("--conference")
    listing.add_argument("--framework", help="exact framework name, slug, or ID")
    listing.add_argument("--organization", help="exact organization name, slug, or ID")
    listing.add_argument("--all-versions", action="store_true")
    listing.add_argument(
        "--order-by",
        choices=("trending", "date_published", "citation_count"),
        default="trending",
    )
    listing.add_argument("--order-dir", choices=("asc", "desc"), default="desc")
    listing.add_argument("--include-resources", action="store_true")
    _json(listing)
    listing.set_defaults(handler=paper_list)
    for name, handler, default_limit, maximum in (
        ("recent", paper_recent, 10, 100),
        ("trending", paper_trending, 20, 100),
    ):
        leaf = paper_commands.add_parser(name, help=f"list {name} papers")
        leaf.add_argument("--limit", type=_limit(maximum), default=default_limit)
        if name == "trending":
            leaf.add_argument(
                "--max-age-days",
                type=lambda value: _bounded(int(value), 1, 365, "--max-age-days"),
                default=180,
            )
            leaf.add_argument("--min-velocity", type=float)
        _json(leaf)
        leaf.set_defaults(handler=handler)
    related = paper_commands.add_parser("related", help="list related papers")
    related.add_argument(
        "paper", help="ArXiv ID, external-paper numeric ID, or exact paper title"
    )
    related.add_argument("--limit", type=_limit(20), default=4)
    _json(related)
    related.set_defaults(handler=paper_related)
    lineage = paper_commands.add_parser("lineage", help="inspect paper lineage")
    lineage_commands = lineage.add_subparsers(dest="lineage_command", required=True)
    lineage_list = lineage_commands.add_parser(
        "list", help="list predecessors and successors"
    )
    lineage_list.add_argument(
        "paper", help="ArXiv ID, external-paper numeric ID, or exact paper title"
    )
    _json(lineage_list)
    lineage_list.set_defaults(handler=paper_lineage)

    task = commands.add_parser("task", help="inspect or list research tasks")
    task.add_argument("--name", help="exact task name, slug, or ID")
    _json(task)
    task.set_defaults(handler=task_detail)
    task_commands = task.add_subparsers(dest="task_command")
    tasks = task_commands.add_parser("list", help="list and filter research tasks")
    tasks.add_argument("--page", type=_page, default=1)
    tasks.add_argument("--page-size", type=_page_size, default=50)
    task_display = tasks.add_mutually_exclusive_group()
    task_display.add_argument(
        "--group-by-area",
        action="store_true",
        help="render the complete visible top-level taxonomy as Markdown",
    )
    task_display.add_argument(
        "--flat",
        action="store_true",
        help="force the paginated flat table in an interactive terminal",
    )
    tasks.add_argument(
        "--area",
        help="case-insensitive exact area name (for example Vision) or area ID",
    )
    tasks.add_argument("--level", type=int)
    tasks.add_argument("--visible-only", action="store_true")
    tasks.add_argument(
        "--order-by",
        choices=("name", "created_at", "level", "paper_count"),
        default="name",
    )
    tasks.add_argument("--order-dir", choices=("asc", "desc"), default="asc")
    _json(tasks)
    tasks.set_defaults(handler=task_list)

    method = commands.add_parser("method", help="inspect or list research methods")
    method.add_argument("--name", help="exact method name, full name, slug, or ID")
    _json(method)
    method.set_defaults(handler=method_detail)
    method_commands = method.add_subparsers(dest="method_command")
    methods = method_commands.add_parser(
        "list", help="list and filter research methods"
    )
    methods.add_argument("--page", type=_page, default=1)
    methods.add_argument("--page-size", type=_method_page_size, default=50)
    methods.add_argument(
        "--area",
        help="case-insensitive exact area name (for example Audio) or area ID",
    )
    methods.add_argument("--introduced-year", type=int)
    methods.add_argument(
        "--order-by",
        choices=("name", "full_name", "introduced_year", "created_at", "paper_count"),
        default="name",
    )
    methods.add_argument("--order-dir", choices=("asc", "desc"), default="asc")
    _json(methods)
    methods.set_defaults(handler=method_list)

    conference = commands.add_parser("conference", help="inspect or list conferences")
    conference.add_argument("--name", help="exact conference name, slug, or ID")
    _json(conference)
    conference.set_defaults(handler=conference_detail)
    conference_commands = conference.add_subparsers(dest="conference_command")
    conferences = conference_commands.add_parser(
        "list", help="list conferences with imported papers"
    )
    conferences.add_argument("--year", type=int)
    _json(conferences)
    conferences.set_defaults(handler=conference_list)

    organization = commands.add_parser(
        "organization", help="inspect or list research organizations"
    )
    organization.add_argument("--name", help="exact organization name, slug, or ID")
    _json(organization)
    organization.set_defaults(handler=organization_detail)
    organization_commands = organization.add_subparsers(dest="organization_command")
    organizations = organization_commands.add_parser(
        "list", help="list research organizations"
    )
    organizations.add_argument(
        "--featured-only",
        action="store_true",
        help="return only curated featured organizations",
    )
    _json(organizations)
    organizations.set_defaults(handler=organization_list)

    framework = commands.add_parser(
        "framework", help="inspect or list research frameworks"
    )
    framework.add_argument("--name", help="exact framework name, slug, or ID")
    _json(framework)
    framework.set_defaults(handler=framework_detail)
    framework_commands = framework.add_subparsers(dest="framework_command")
    frameworks = framework_commands.add_parser("list", help="list research frameworks")
    frameworks.add_argument("--domain", help="exact framework domain name or slug")
    frameworks.add_argument("--category", help="exact framework category name or slug")
    frameworks.add_argument("--platform", help="exact platform name")
    _json(frameworks)
    frameworks.set_defaults(handler=framework_list)

    benchmark = commands.add_parser("benchmark", help="inspect benchmarks")
    benchmark.add_argument(
        "--name", help="exact benchmark name, full name, slug, or ID"
    )
    benchmark.add_argument(
        "--limit",
        type=_limit(),
        default=20,
        help="maximum leaderboard rows, 1-100 (default: 20)",
    )
    benchmark.add_argument("--is-open", choices=("true", "false"))
    benchmark.add_argument(
        "--require-metrics",
        type=_metric_names,
        default=(),
        metavar="METRIC[,METRIC]",
        help="keep rows containing numeric values for every named metric",
    )
    benchmark.add_argument(
        "--min",
        dest="minimum_metrics",
        type=_metric_bound,
        action="append",
        default=[],
        metavar="METRIC=VALUE",
        help="keep rows at or above a metric threshold; repeatable",
    )
    benchmark.add_argument(
        "--max",
        dest="maximum_metrics",
        type=_metric_bound,
        action="append",
        default=[],
        metavar="METRIC=VALUE",
        help="keep rows at or below a metric threshold; repeatable",
    )
    benchmark.add_argument(
        "--sort",
        dest="sort_metric",
        type=_metric_sort,
        metavar="METRIC[:asc|desc]",
        help="sort matching rows by a numeric metric (default direction: desc)",
    )
    benchmark.add_argument(
        "--pareto",
        type=_pareto_objectives,
        metavar="METRIC:higher,METRIC:lower",
        help="keep the multi-metric Pareto frontier",
    )
    _json(benchmark)
    benchmark.set_defaults(handler=benchmark_detail)
    benchmark_commands = benchmark.add_subparsers(dest="benchmark_command")
    benchmarks = benchmark_commands.add_parser(
        "list", help="list and filter benchmarks"
    )
    benchmarks.add_argument("--page", type=_page, default=1)
    benchmarks.add_argument(
        "--page-size",
        "--limit",
        dest="page_size",
        type=_page_size,
        default=50,
        help="results per page, 1-100 (default: 50)",
    )
    benchmarks.add_argument("--search")
    benchmarks.add_argument("--task")
    benchmark_display = benchmarks.add_mutually_exclusive_group()
    benchmark_display.add_argument(
        "--group-by-area",
        action="store_true",
        help="render top benchmarks under each visible task as Markdown",
    )
    benchmark_display.add_argument(
        "--flat",
        action="store_true",
        help="force the paginated flat table in an interactive terminal",
    )
    benchmarks.add_argument(
        "--area",
        help="case-insensitive exact area name or ID for grouped output",
    )
    benchmarks.add_argument(
        "--benchmarks-per-task",
        type=_benchmarks_per_task,
        default=3,
        help="top benchmarks per task in grouped output, 1-10 (default: 3)",
    )
    benchmarks.add_argument("--include-descendants", action="store_true")
    benchmarks.add_argument("--min-eval-count", type=int)
    benchmarks.add_argument("--is-open", choices=("true", "false"))
    benchmarks.add_argument(
        "--order-by",
        choices=("trending", "name", "full_name", "created_at", "paper_count"),
        help="defaults to trending for task lists and name otherwise",
    )
    benchmarks.add_argument("--order-dir", choices=("asc", "desc"), default="asc")
    _json(benchmarks)
    benchmarks.set_defaults(handler=benchmark_list)
    skills = commands.add_parser("skills", help="manage the Skill for AI coding agents")
    skill_commands = skills.add_subparsers(dest="skills_command", required=True)
    add_skill = skill_commands.add_parser(
        "add",
        help="install the version-matched pwc CLI Skill",
        description=(
            "Install the pwc CLI Skill for Codex, Cursor, OpenCode, Claude Code, "
            "or another coding-agent harness."
        ),
    )
    add_skill.add_argument(
        "--global",
        "-g",
        dest="global_",
        action="store_true",
        help="install in the user-level skills directory",
    )
    add_skill.add_argument(
        "--claude",
        action="store_true",
        help="also link the Skill into Claude's skills directory",
    )
    add_skill.add_argument(
        "--dest",
        type=Path,
        help="install into a custom skills directory",
    )
    add_skill.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing pwc CLI Skill",
    )
    add_skill.set_defaults(handler=skills_add)
    version_parser = commands.add_parser(
        "version", help="show CLI and API contract versions"
    )
    version_parser.set_defaults(handler=version)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args, Client())
    except TransportError as error:
        print(f"pwc: {error}", file=sys.stderr)
        return 3
    except UsageError as error:
        print(f"pwc: {error}", file=sys.stderr)
        return 2
    except SkillInstallError as error:
        print(f"pwc: {error}", file=sys.stderr)
        return 2
    except ResponseError as error:
        print(f"pwc: {error}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
