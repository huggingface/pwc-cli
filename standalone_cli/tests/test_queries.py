"""The in-process query layer must mirror ``pwc ... --json`` exactly."""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout

import pytest
from pwc_cli import queries
from pwc_cli.cli import UsageError, build_parser, main
from pwc_cli.transport import Response

NON_RESEARCH_COMMANDS = {
    ("skills", "add"),
    ("version",),
    ("auth", "login"),
    ("auth", "status"),
    ("auth", "logout"),
    ("paper", "edit", "export"),
    ("paper", "edit", "preview"),
    ("paper", "edit", "submit"),
}

EVALUATION_ROWS = [
    {
        "id": "1",
        "model_name": "Small",
        "metrics": {"mAP": 50.0},
        "best_rank": 2,
        "num_parameters": 100_000_000,
        "paper_id": "1",
        "task_id": "t",
        "dataset_id": "9",
    },
    {
        "id": "2",
        "model_name": "Smaller",
        "metrics": {"mAP": 60.0},
        "best_rank": 1,
        "num_parameters": 50_000_000,
        "paper_id": "2",
        "task_id": "t",
        "dataset_id": "9",
    },
]

BENCHMARK_ROUTES = {
    "datasets/": {"results": [{"id": "9", "name": "COCO", "slug": "coco"}]},
    "datasets/9/evaluations/": {"count": 2, "results": EVALUATION_ROWS},
    "evaluations/": {
        "count": 2,
        "next_page": None,
        "parameter_coverage_known": 2,
        "parameter_coverage_total": 2,
        "results": EVALUATION_ROWS,
    },
}


class StubClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        return Response(json.dumps(self.routes[path]).encode(), {})


def _leaf_commands(parser, prefix=()):
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, child in action.choices.items():
            path = (*prefix, name)
            if "handler" in child._defaults:
                yield path
            yield from _leaf_commands(child, path)


def test_read_only_commands_cover_every_research_command():
    research = set(_leaf_commands(build_parser())) - NON_RESEARCH_COMMANDS

    assert set(queries.READ_ONLY_COMMANDS) == research
    for command in queries.READ_ONLY_COMMANDS:
        assert "handler" in queries.command_parser(command)._defaults


def test_query_options_exclude_presentation_flags_and_selectors():
    options = queries.query_options(("benchmark", "list"))

    assert set(options) == {
        "page",
        "page_size",
        "search",
        "task",
        "group_by_area",
        "area",
        "benchmarks_per_task",
        "include_descendants",
        "min_eval_count",
        "is_open",
        "order_by",
        "order_dir",
    }
    assert queries.query_options(("search",))["query"].option_strings == []


def test_build_argv_translates_structured_options():
    argv = queries.build_argv(
        ("benchmark",),
        {
            "name": "COCO",
            "limit": 5,
            "is_open": True,
            "require_metrics": ["mAP", "FPS"],
            "minimum_metrics": {"mAP": 40},
            "maximum_metrics": {"FPS": 10.5},
            "pareto": ["mAP:higher", "FPS:lower"],
            "max_parameters": None,
        },
    )

    assert argv == [
        "benchmark",
        "--name",
        "COCO",
        "--limit",
        "5",
        "--is-open",
        "true",
        "--require-metrics",
        "mAP,FPS",
        "--min",
        "mAP=40",
        "--max",
        "FPS=10.5",
        "--pareto",
        "mAP:higher,FPS:lower",
    ]
    assert queries.build_argv(
        ("paper", "list"),
        {"author": ["Ada", "@ada"], "all_versions": True, "include_resources": False},
    ) == ["paper", "list", "--author", "Ada", "--author", "@ada", "--all-versions"]

    argv = queries.build_argv(("search",), {"query": "-attention", "limit": 3})
    assert argv == ["search", "--limit", "3", "--", "-attention"]
    assert build_parser().parse_args(argv).query == "-attention"


def test_query_returns_exactly_the_cli_json_data(monkeypatch, capsys):
    monkeypatch.setattr("pwc_cli.cli.Client", lambda: StubClient(BENCHMARK_ROUTES))
    output = io.StringIO()
    with redirect_stdout(output):
        assert (
            main(
                [
                    "benchmark",
                    "--name",
                    "COCO",
                    "--max-parameters",
                    "500M",
                    "--sort",
                    "mAP",
                    "--limit",
                    "1",
                    "--json",
                ]
            )
            == 0
        )
    printed = json.loads(output.getvalue())

    client = StubClient(BENCHMARK_ROUTES)
    data = queries.query(
        ("benchmark",),
        {"name": "COCO", "max_parameters": "500M", "sort_metric": "mAP", "limit": 1},
        client,
    )

    assert {"schema_version": "v1", "data": data} == printed
    assert data["matched_count"] == 2
    assert [row["model_name"] for row in data["results"]] == ["Smaller"]
    assert client.calls[1][1]["max_parameters_exclusive"] == 500_000_001
    assert capsys.readouterr().out == ""


def test_query_raises_usage_errors_instead_of_exiting():
    client = StubClient({})

    with pytest.raises(UsageError, match="--limit must be 1-100"):
        queries.query(("search",), {"query": "x", "limit": 500}, client)
    with pytest.raises(UsageError, match="unknown option"):
        queries.query(("search",), {"query": "x", "page_size": 5}, client)
    with pytest.raises(UsageError, match="invalid"):
        queries.query(("search",), {"query": "x", "start_date": "2026-13-01"}, client)
    with pytest.raises(UsageError, match="cannot be combined"):
        queries.query(("task", "list"), {"group_by_area": True, "page": 2}, client)
    with pytest.raises(UsageError, match="unknown metric"):
        queries.query(
            ("benchmark",),
            {"name": "COCO", "sort_metric": "Latency"},
            StubClient(BENCHMARK_ROUTES),
        )
    assert client.calls == []


def test_query_refuses_commands_that_are_not_read_only():
    for command in (("skills", "add"), ("version",), ("paper", "edit", "export")):
        with pytest.raises(UsageError, match="not a read-only"):
            queries.query(command, {}, StubClient({}))
