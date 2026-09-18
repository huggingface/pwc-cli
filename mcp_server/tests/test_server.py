from __future__ import annotations

import asyncio
import logging

from mcp.client import Client
from pwc_cli.cli import UsageError
from pwc_cli.transport import HTTPStatusError, ResponseError
from pwc_mcp.catalog import PaperMarkdownChunk
from pwc_mcp.server import TOOL_COMMANDS, build_server

PAPER_ROW = {
    "id": "755",
    "arxiv_id": "1706.03762",
    "title": "Attention Is All You Need",
    "authors": ["Ashish Vaswani"],
    "published": "2017-06-12",
    "citation_count": 190_373,
    "url_abs": "https://arxiv.org/abs/1706.03762v7",
    "has_official_implementation": True,
    "code_repository_count": 595,
}
PAPER_PAGE = {"next_page": 2, "results": [PAPER_ROW]}
PAPER_INFO = {
    **PAPER_ROW,
    "abstract": "A transformer architecture.",
    "url_pdf": "https://arxiv.org/pdf/1706.03762v7.pdf",
    "tasks": [
        {"id": "6", "name": "Machine Translation", "slug": "machine-translation"}
    ],
    "methods": [{"id": "1", "name": "Transformer", "slug": "transformer"}],
    "repositories": [
        {"url": "https://github.com/tensorflow/tensor2tensor", "is_official": True}
    ],
    "project_pages": ["https://example.test/transformer"],
    "hf_models": ["https://huggingface.co/google-t5/t5-base"],
    "hf_datasets": [],
    "hf_spaces": [],
}
PAPER_EVALUATION = {
    "id": "40",
    "dataset_name": "WMT2014 English-German",
    "task_name": "Machine Translation",
    "model_name": "Transformer Big",
    "metrics": {"BLEU score": 28.4},
    "best_metric": "BLEU score",
    "best_rank": 3,
    "is_open": True,
    "num_parameters": 213_000_000,
    "result_url": "https://example.test/result",
}
EVALUATION_ROW = {
    "id": "10",
    "model_name": "ExampleNet",
    "harness": "timm",
    "metrics": {"Accuracy": 90.1},
    "best_metric": "Accuracy",
    "best_rank": 1,
    "task_name": "Image Classification",
    "paper_id": "755",
    "paper_title": "Attention Is All You Need",
    "paper_arxiv_id": "1706.03762",
    "paper_published_date": "2017-06-12",
    "is_open": True,
    "num_parameters": 1000,
}
BENCHMARK = {
    "id": "72",
    "name": "ImageNet-1k",
    "slug": "imagenet-1k",
    "paper_count": 124,
}
TASK = {
    "id": "1",
    "name": "Image Classification",
    "slug": "image-classification",
    "description": "Assign a class to an image.",
    "paper_count": 2343,
}
METHOD = {
    "id": "2",
    "name": "Transformer",
    "slug": "transformer",
    "full_name": "Transformer",
    "description": "Attention-based architecture.",
    "introduced_year": 2017,
    "source_paper_id": "755",
    "source_url": "/paper/1706.03762",
    "source_title": "Attention Is All You Need",
    "paper_count": 13505,
}
CONFERENCE = {"slug": "cvpr-2025", "name": "CVPR 2025", "year": 2025, "paper_count": 3}
ORGANIZATION = {"id": "4", "slug": "nvidia", "name": "NVIDIA", "paper_count": 900}
FRAMEWORK = {"id": "7", "slug": "vllm", "name": "vLLM", "platforms": ["gpu"]}
GROUPED_BENCHMARKS = {
    "results": [
        {
            "id": "1",
            "name": "Vision",
            "tasks": [
                {
                    "slug": "image-classification",
                    "benchmarks": [
                        {
                            "id": "72",
                            "name": "ImageNet-1k",
                            "slug": "imagenet-1k",
                            "evaluation_count": 124,
                        }
                    ],
                }
            ],
        }
    ]
}

PAYLOADS = {
    ("search",): PAPER_PAGE,
    ("paper", "list"): PAPER_PAGE,
    # The recent and trending endpoints return a bare list of papers.
    ("paper", "recent"): [PAPER_ROW],
    ("paper", "trending"): [PAPER_ROW],
    ("paper", "related"): {"results": [PAPER_ROW]},
    ("paper", "evaluations"): {
        "count": 1,
        "page": 1,
        "next_page": 2,
        "results": [EVALUATION_ROW],
    },
    ("paper", "lineage", "list"): {
        "paper": {
            "id": 755,
            "reference": "1706.03762",
            "title": "Attention Is All You Need",
        },
        "predecessors": [],
        "successors": [{"id": 900, "reference": "2001.00001", "title": "A Follow-up"}],
    },
    ("task",): {
        "task": TASK,
        "area": {"id": "1", "name": "Vision"},
        "parents": [],
        "children": [],
        "benchmarks": [BENCHMARK],
        "common_methods": [METHOD],
        "papers": [PAPER_ROW],
        "paper_count": 2343,
    },
    ("task", "list"): {"count": 1, "results": [TASK]},
    ("method",): {"method": METHOD, "area": {"id": "1", "name": "Vision"}},
    ("method", "list"): {"count": 1, "results": [METHOD]},
    ("conference",): CONFERENCE,
    ("conference", "list"): {"count": 1, "results": [CONFERENCE]},
    ("organization",): ORGANIZATION,
    ("organization", "list"): {"count": 1, "results": [ORGANIZATION]},
    ("framework",): FRAMEWORK,
    ("framework", "list"): {"count": 1, "results": [FRAMEWORK]},
    ("benchmark",): {
        "benchmark": {"id": "72", "name": "ImageNet-1k", "slug": "imagenet-1k"},
        "count": 1,
        "matched_count": 1,
        "page": 1,
        "next_page": 2,
        "results": [EVALUATION_ROW],
    },
    ("benchmark", "list"): {"next_page": None, "results": [BENCHMARK]},
}


class StubCatalog:
    """Records the CLI command and options each tool requests."""

    def __init__(self):
        self.queries: list[tuple[tuple[str, ...], dict]] = []
        self.resolve_calls = 0
        self.read_calls = []

    def resolve_paper(self, paper: str):
        self.resolve_calls += 1
        assert paper == "1706.03762"
        return paper

    def read_paper_chunk(
        self,
        paper: str,
        *,
        offset: int = 0,
        content_version: str | None = None,
        limit: int = 65_536,
        resolved: bool = False,
    ):
        assert paper == "1706.03762"
        assert resolved is True
        version = "a" * 64
        assert content_version in {None, version}
        self.read_calls.append((offset, content_version, limit))
        raw = b"abcdefgh"
        markdown = raw[offset : offset + limit].decode()
        next_offset = (
            offset + len(markdown) if offset + len(markdown) < len(raw) else None
        )
        return PaperMarkdownChunk(
            paper=paper,
            source="arxiv",
            markdown=markdown,
            content_version=version,
            next_offset=next_offset,
        )

    def query(self, command, options):
        self.queries.append((tuple(command), dict(options)))
        if command == ("paper", "info"):
            payload = dict(PAPER_INFO)
            if options.get("include_evals"):
                payload["evaluations"] = {"count": 1, "results": [PAPER_EVALUATION]}
            return payload
        if command == ("benchmark", "list") and (
            options.get("group_by_area") or options.get("area")
        ):
            return GROUPED_BENCHMARKS
        return PAYLOADS[tuple(command)]

    def options(self, command):
        return next(options for called, options in self.queries if called == command)


def _call(catalog, requests, **server_options):
    async def exercise():
        async with Client(build_server(catalog, **server_options)) as client:
            results = []
            for name, arguments in requests:
                results.append(await client.call_tool(name, arguments))
            return results

    return asyncio.run(exercise())


def test_search_papers_is_a_read_only_structured_tool():
    catalog = StubCatalog()

    async def exercise():
        async with Client(build_server(catalog)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            result = await client.call_tool(
                "search_papers",
                {"query": "transformer", "mode": "keyword", "limit": 1},
            )
        return tools, result

    tools, result = asyncio.run(exercise())

    assert tools["search_papers"].annotations.read_only_hint is True
    assert tools["search_papers"].input_schema["properties"]["limit"]["maximum"] == 25
    assert tools["search_papers"].input_schema["properties"]["mode"]["enum"] == [
        "hybrid",
        "keyword",
        "semantic",
    ]
    # Same default as `pwc search --mode`.
    assert (
        tools["search_papers"].input_schema["properties"]["mode"]["default"] == "hybrid"
    )
    assert result.is_error is False
    assert result.structured_content == {
        "schema_version": "v1",
        "items": [
            {
                "id": "755",
                "arxiv_id": "1706.03762",
                "title": "Attention Is All You Need",
                "authors": ["Ashish Vaswani"],
                "published": "2017-06-12",
                "citation_count": 190_373,
                "url": "https://arxiv.org/abs/1706.03762v7",
                "has_official_implementation": True,
                "code_repository_count": 595,
            }
        ],
        "next_page": 2,
        "data": PAPER_PAGE,
    }
    assert catalog.queries == [
        (
            ("search",),
            {
                "query": "transformer",
                "mode": "keyword",
                "page": 1,
                "limit": 1,
                "start_date": None,
                "end_date": None,
                "has_official_implementation": False,
            },
        )
    ]


def test_search_rejects_invalid_ranges_before_calling_the_catalog():
    catalog = StubCatalog()
    (result,) = _call(
        catalog,
        [
            (
                "search_papers",
                {
                    "query": "transformer",
                    "published_after": "2026-08-31",
                    "published_before": "2026-08-01",
                },
            )
        ],
    )

    assert result.is_error is True
    assert result.content[0].text == (
        "Error executing tool search_papers: "
        "published_after must be on or before published_before"
    )
    assert catalog.queries == []


def test_catalog_failures_do_not_expose_or_log_user_queries(caplog):
    secret_query = "private unreleased project heliotrope"

    class FailingCatalog(StubCatalog):
        def query(self, command, options):
            raise ResponseError(f"API returned invalid JSON for {secret_query}")

    with caplog.at_level(logging.INFO):
        (result,) = _call(
            FailingCatalog(), [("search_papers", {"query": secret_query})]
        )

    assert result.is_error is True
    assert result.content[0].text == (
        "Error executing tool search_papers: "
        "upstream_error: the Papers With Code catalog request failed"
    )
    assert secret_query not in caplog.text


def test_lookup_failures_return_the_cli_hint_without_logging_it(caplog):
    class MissingCatalog(StubCatalog):
        def query(self, command, options):
            raise ResponseError(
                "Task not found: language-modelling; closest results: Language Modeling"
            )

    class TransportCatalog(StubCatalog):
        def query(self, command, options):
            raise HTTPStatusError(404, "not found: language-modelling")

    with caplog.at_level(logging.INFO):
        (missing,) = _call(
            MissingCatalog(), [("get_task", {"task": "language-modelling"})]
        )
        (transport,) = _call(
            TransportCatalog(), [("get_task", {"task": "language-modelling"})]
        )

    assert missing.content[0].text == (
        "Error executing tool get_task: "
        "not_found: Task not found: language-modelling; closest results: Language Modeling"
    )
    assert transport.content[0].text == (
        "Error executing tool get_task: not_found: the requested catalog record does not exist"
    )
    assert "language-modelling" not in caplog.text


def test_cli_usage_errors_are_returned_verbatim():
    class StrictCatalog(StubCatalog):
        def query(self, command, options):
            raise UsageError("unknown metric(s): latency; available metrics: Accuracy")

    (result,) = _call(
        StrictCatalog(),
        [("get_benchmark", {"benchmark": "imagenet-1k", "sort_metric": "latency"})],
    )

    assert result.is_error is True
    assert result.content[0].text == (
        "Error executing tool get_benchmark: "
        "unknown metric(s): latency; available metrics: Accuracy"
    )


def test_paper_info_and_reading_use_stable_schemas_and_opaque_continuation():
    catalog = StubCatalog()

    async def exercise():
        async with Client(build_server(catalog, read_chunk_bytes=5)) as client:
            info = await client.call_tool("get_paper_info", {"paper": "1706.03762"})
            evaluated = await client.call_tool(
                "get_paper_info",
                {"paper": "1706.03762", "include_evaluations": True},
            )
            first = await client.call_tool("read_paper", {"paper": "1706.03762"})
            second = await client.call_tool(
                "read_paper",
                {
                    "paper": "1706.03762",
                    "cursor": first.structured_content["next_cursor"],
                },
            )
        return info, evaluated, first, second

    info, evaluated, first, second = asyncio.run(exercise())

    assert catalog.queries[0] == (
        ("paper", "info"),
        {"paper": "1706.03762", "include_resources": True, "include_evals": False},
    )
    paper = info.structured_content["paper"]
    assert paper["title"] == "Attention Is All You Need"
    assert paper["tasks"] == [
        {"id": "6", "name": "Machine Translation", "slug": "machine-translation"}
    ]
    assert paper["repositories"][0]["is_official"] is True
    assert paper["hf_models"] == []
    assert paper["code_repository_count"] == 595
    assert info.structured_content["evaluations"] is None
    assert info.structured_content["data"] is None

    assert catalog.queries[1][1]["include_evals"] is True
    assert evaluated.structured_content["evaluation_count"] == 1
    assert evaluated.structured_content["evaluations"] == [
        {
            "id": "40",
            "benchmark": "WMT2014 English-German",
            "task": "Machine Translation",
            "model_name": "Transformer Big",
            "harness": None,
            "metrics": {"BLEU score": 28.4},
            "best_metric": "BLEU score",
            "best_rank": 3,
            "is_open": True,
            "num_parameters": 213_000_000,
            "source_url": "https://example.test/result",
        }
    ]

    assert first.structured_content["markdown"] == "abcde"
    assert first.structured_content["truncated"] is True
    assert first.structured_content["next_cursor"]
    assert second.structured_content == {
        "schema_version": "v1",
        "paper": "1706.03762",
        "markdown": "fgh",
        "truncated": False,
        "next_cursor": None,
    }
    assert catalog.resolve_calls == 1
    assert catalog.read_calls == [(0, None, 5), (5, "a" * 64, 5)]


def test_paper_evaluations_are_paginated_and_compact():
    catalog = StubCatalog()
    (result,) = _call(
        catalog,
        [("get_paper_evaluations", {"paper": "1706.03762", "limit": 5})],
    )

    assert catalog.options(("paper", "evaluations")) == {
        "paper": "1706.03762",
        "page": 1,
        "page_size": 5,
    }
    assert result.structured_content["next_page"] == 2
    assert result.structured_content["data"] is None
    assert (
        result.structured_content["evaluations"][0]["rank_scopes"][0]["task_name"]
        == "Image Classification"
    )


def test_read_paper_rejects_invalid_continuation_as_an_expected_error():
    (result,) = _call(
        StubCatalog(),
        [("read_paper", {"paper": "1706.03762", "cursor": "%%%private%%%"})],
    )

    assert result.is_error is True
    assert result.content[0].text == (
        "Error executing tool read_paper: invalid continuation cursor"
    )


def test_paper_listing_related_work_and_lineage_are_composable():
    catalog = StubCatalog()
    listed, recent, trending, related, lineage = _call(
        catalog,
        [
            (
                "list_papers",
                {
                    "task": "machine-translation",
                    "authors": ["Ashish Vaswani"],
                    "limit": 1,
                },
            ),
            ("list_recent_papers", {"limit": 3}),
            ("list_trending_papers", {"max_age_days": 30, "min_velocity": 1.5}),
            ("get_related_papers", {"paper": "1706.03762", "limit": 2}),
            ("get_paper_lineage", {"paper": "1706.03762"}),
        ],
    )

    assert catalog.options(("paper", "list")) == {
        "search": None,
        "task": "machine-translation",
        "method": None,
        "conference": None,
        "framework": None,
        "organization": None,
        "author": ["Ashish Vaswani"],
        "start_date": None,
        "end_date": None,
        "all_versions": False,
        "order_by": "trending",
        "order_dir": "desc",
        "include_resources": False,
        "has_official_implementation": False,
        "page": 1,
        "page_size": 1,
    }
    assert catalog.options(("paper", "recent")) == {"limit": 3}
    assert catalog.options(("paper", "trending")) == {
        "limit": 20,
        "max_age_days": 30,
        "min_velocity": 1.5,
    }
    assert catalog.options(("paper", "related")) == {"paper": "1706.03762", "limit": 2}
    assert catalog.options(("paper", "lineage", "list")) == {"paper": "1706.03762"}
    assert listed.structured_content["items"][0]["arxiv_id"] == "1706.03762"
    assert recent.structured_content["next_page"] is None
    assert recent.structured_content["items"][0]["id"] == "755"
    assert recent.structured_content["data"] == [PAPER_ROW]
    assert trending.structured_content["items"][0]["id"] == "755"
    assert related.structured_content["items"][0]["id"] == "755"
    assert lineage.structured_content["paper"]["reference"] == "1706.03762"
    assert lineage.structured_content["successors"] == [
        {"id": "900", "reference": "2001.00001", "title": "A Follow-up"}
    ]


def test_taxonomy_and_benchmark_tools_return_stable_catalog_entities():
    catalog = StubCatalog()

    async def exercise():
        async with Client(build_server(catalog)) as client:
            tool_names = {tool.name for tool in (await client.list_tools()).tools}
            task = await client.call_tool("get_task", {"task": "image-classification"})
            method = await client.call_tool("get_method", {"method": "transformer"})
            benchmarks = await client.call_tool(
                "list_benchmarks", {"task": "image-classification"}
            )
            benchmark = await client.call_tool(
                "get_benchmark",
                {
                    "benchmark": "imagenet-1k",
                    "limit": 5,
                    "is_open": True,
                    "max_parameters": "4B",
                    "minimum_metrics": {"Accuracy": 80},
                    "sort_metric": "Accuracy:desc",
                },
            )
        return tool_names, task, method, benchmarks, benchmark

    tool_names, task, method, benchmarks, benchmark = asyncio.run(exercise())

    assert tool_names == set(TOOL_COMMANDS)
    assert len(tool_names) == 21
    assert catalog.options(("task",)) == {"name": "image-classification"}
    assert catalog.options(("method",)) == {"name": "transformer"}
    assert catalog.options(("benchmark", "list")) == {
        "search": None,
        "task": "image-classification",
        "include_descendants": False,
        "min_eval_count": None,
        "is_open": None,
        "group_by_area": False,
        "area": None,
        "benchmarks_per_task": 3,
        "order_by": None,
        "order_dir": "asc",
        "page": 1,
        "page_size": 25,
    }
    assert catalog.options(("benchmark",)) == {
        "name": "imagenet-1k",
        "page": 1,
        "limit": 5,
        "is_open": True,
        "max_parameters": "4B",
        "require_metrics": None,
        "minimum_metrics": {"Accuracy": 80.0},
        "maximum_metrics": None,
        "sort_metric": "Accuracy:desc",
        "pareto": None,
    }
    assert task.structured_content["task"]["area"] == {"id": "1", "name": "Vision"}
    assert task.structured_content["task"]["benchmarks"][0]["slug"] == "imagenet-1k"
    assert task.structured_content["data"] is None
    assert method.structured_content["method"]["introduced_year"] == 2017
    assert benchmarks.structured_content["items"][0]["slug"] == "imagenet-1k"
    assert benchmark.structured_content["matched_count"] == 1
    assert benchmark.structured_content["next_page"] == 2
    evaluation = benchmark.structured_content["evaluations"][0]
    assert evaluation["model_name"] == "ExampleNet"
    assert evaluation["metrics"] == {"Accuracy": 90.1}
    assert evaluation["rank_scopes"] == [
        {
            "task_id": None,
            "task_name": "Image Classification",
            "task_slug": None,
            "rank": 1,
        }
    ]
    assert benchmark.structured_content["metric_directions"] == {"Accuracy": "higher"}


def test_grouped_listings_omit_pagination_and_flatten_benchmarks():
    catalog = StubCatalog()
    tasks, grouped_tasks, benchmarks = _call(
        catalog,
        [
            ("list_tasks", {"area": "Vision", "level": 1}),
            ("list_tasks", {"group_by_area": True}),
            ("list_benchmarks", {"area": "Vision", "benchmarks_per_task": 2}),
        ],
    )

    assert catalog.queries[0] == (
        ("task", "list"),
        {
            "search": None,
            "area": "Vision",
            "level": 1,
            "visible_only": False,
            "group_by_area": False,
            "order_by": "name",
            "order_dir": "asc",
            "page": 1,
            "page_size": 25,
        },
    )
    assert catalog.queries[1][1]["group_by_area"] is True
    assert catalog.queries[1][1]["page_size"] is None
    assert catalog.queries[2][1]["page_size"] is None
    assert catalog.queries[2][1]["benchmarks_per_task"] == 2
    assert tasks.structured_content == {
        "schema_version": "v1",
        "data": {"count": 1, "results": [TASK]},
    }
    assert grouped_tasks.structured_content["data"] == {"count": 1, "results": [TASK]}
    assert benchmarks.structured_content["items"] == [
        {
            "id": "72",
            "name": "ImageNet-1k",
            "slug": "imagenet-1k",
            "full_name": None,
            "description": None,
            "split": None,
            "hf_url": None,
            "paper_count": 124,
        }
    ]
    assert benchmarks.structured_content["data"] == GROUPED_BENCHMARKS


def test_new_catalog_tools_return_the_cli_json_document():
    catalog = StubCatalog()
    requests = [
        ("list_methods", {"introduced_year": 2017, "order_by": "paper_count"}),
        ("get_conference", {"conference": "CVPR 2025"}),
        ("list_conferences", {"year": 2025}),
        ("get_organization", {"organization": "NVIDIA"}),
        ("list_organizations", {"featured_only": True}),
        ("get_framework", {"framework": "vLLM"}),
        ("list_frameworks", {"platform": "gpu"}),
    ]
    results = _call(catalog, requests)

    for (name, _arguments), result in zip(requests, results):
        assert result.is_error is False, name
        assert result.structured_content == {
            "schema_version": "v1",
            "data": PAYLOADS[TOOL_COMMANDS[name]],
        }
    assert catalog.options(("method", "list")) == {
        "search": None,
        "area": None,
        "introduced_year": 2017,
        "order_by": "paper_count",
        "order_dir": "asc",
        "page": 1,
        "page_size": 25,
    }
    assert catalog.options(("conference",)) == {"name": "CVPR 2025"}
    assert catalog.options(("conference", "list")) == {"year": 2025}
    assert catalog.options(("organization",)) == {"name": "NVIDIA"}
    assert catalog.options(("organization", "list")) == {"featured_only": True}
    assert catalog.options(("framework",)) == {"name": "vLLM"}
    assert catalog.options(("framework", "list")) == {
        "domain": None,
        "category": None,
        "platform": "gpu",
    }


def test_resources_expose_canonical_papers_tasks_and_benchmarks():
    async def exercise():
        async with Client(build_server(StubCatalog())) as client:
            templates = {
                template.uri_template
                for template in (
                    await client.list_resource_templates()
                ).resource_templates
            }
            paper = await client.read_resource("pwc://papers/1706.03762")
            markdown = await client.read_resource("pwc://papers/1706.03762/markdown")
            task = await client.read_resource("pwc://tasks/image-classification")
            benchmark = await client.read_resource("pwc://benchmarks/imagenet-1k")
        return templates, paper, markdown, task, benchmark

    templates, paper, markdown, task, benchmark = asyncio.run(exercise())

    assert templates == {
        "pwc://papers/{paper}",
        "pwc://papers/{paper}/markdown",
        "pwc://tasks/{task}",
        "pwc://benchmarks/{benchmark}",
    }
    assert '"title":"Attention Is All You Need"' in paper.contents[0].text
    assert markdown.contents[0].text == "abcdefgh"
    assert '"slug":"image-classification"' in task.contents[0].text
    assert '"slug":"imagenet-1k"' in benchmark.contents[0].text


def test_upstream_validation_rate_limit_and_timeout_errors_are_actionable(caplog):
    from pwc_cli.transport import TransportError
    from pwc_mcp.server import catalog_error_message

    class ErrorCatalog(StubCatalog):
        def __init__(self, error):
            super().__init__()
            self.error = error

        def query(self, command, options):
            raise self.error

    cases = {
        HTTPStatusError(422, '{"detail":"page_size must be <= 100"}\n\x00'): (
            'invalid_argument: {"detail":"page_size must be <= 100"}'
        ),
        HTTPStatusError(429, "slow down"): (
            "rate_limited: the Papers With Code catalog is rate limiting; retry later"
        ),
        TransportError("API request timed out"): (
            "upstream_timeout: the Papers With Code catalog timed out"
        ),
        ResponseError("Paper not found: 2308.10195"): (
            "not_found: Paper not found: 2308.10195"
        ),
        ResponseError("Paper URL not supported: https://doi.org/x; only arXiv"): (
            "not_found: Paper URL not supported: https://doi.org/x; only arXiv"
        ),
    }
    for error, expected in cases.items():
        (result,) = _call(ErrorCatalog(error), [("get_task", {"task": "x"})])
        assert result.is_error is True
        assert result.content[0].text == f"Error executing tool get_task: {expected}"

    with caplog.at_level(logging.WARNING):
        assert catalog_error_message(ResponseError("API returned invalid JSON")) == (
            "upstream_error: the Papers With Code catalog request failed"
        )
    assert "pwc-mcp generic catalog error type=ResponseError" in caplog.text
    assert "invalid JSON" not in caplog.text


def test_list_benchmarks_falls_back_instead_of_rejecting_argument_combinations():
    catalog = StubCatalog()
    trending_search, area_with_search, area_only = _call(
        catalog,
        [
            (
                "list_benchmarks",
                {"search": "COCO", "order_by": "trending", "order_direction": "desc"},
            ),
            ("list_benchmarks", {"area": "Vision", "search": "HDR", "limit": 10}),
            ("list_benchmarks", {"area": "Vision", "limit": 10}),
        ],
    )

    # order_by=trending needs a task; the CLI would raise a usage error.
    assert catalog.queries[0][1]["order_by"] is None
    assert catalog.queries[0][1]["search"] == "COCO"
    assert trending_search.is_error is False
    assert "Note: order_by=trending needs task; ordered by name instead." in (
        trending_search.content[0].text
    )
    assert trending_search.structured_content["items"][0]["slug"] == "imagenet-1k"

    # area cannot be combined with flat filters; the filters win.
    assert catalog.queries[1][1]["area"] is None
    assert catalog.queries[1][1]["search"] == "HDR"
    assert catalog.queries[1][1]["page_size"] == 10
    assert "area cannot be combined with filters" in area_with_search.content[0].text

    # A bare limit does not conflict with grouping: the grouped listing ignores it.
    assert catalog.queries[2][1]["area"] == "Vision"
    assert catalog.queries[2][1]["page_size"] is None
    assert "Note:" not in area_only.content[0].text
    assert area_only.content[0].text == "Found 1 benchmarks."


def test_read_paper_names_an_identity_mismatch_instead_of_a_generic_failure():
    class DriftingCatalog(StubCatalog):
        def read_paper_chunk(self, paper, **kwargs):
            chunk = super().read_paper_chunk(paper, **kwargs)
            return PaperMarkdownChunk(
                paper="9999.99999",
                source=chunk.source,
                markdown=chunk.markdown,
                content_version=chunk.content_version,
                next_offset=chunk.next_offset,
            )

    (result,) = _call(DriftingCatalog(), [("read_paper", {"paper": "1706.03762"})])

    assert result.is_error is True
    assert result.content[0].text == (
        "Error executing tool read_paper: paper changed; restart reading from the beginning"
    )
