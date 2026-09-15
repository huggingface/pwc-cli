from __future__ import annotations

import asyncio
import logging

from mcp.client import Client
from pwc_cli.transport import ResponseError
from pwc_mcp.server import build_server


class StubCatalog:
    def search_papers(self, **_kwargs):
        return {
            "next_page": 2,
            "results": [
                {
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
            ],
        }

    def get_paper_info(self, paper: str, *, include_resources: bool):
        assert paper == "1706.03762"
        assert include_resources is True
        return {
            "id": "755",
            "arxiv_id": "1706.03762",
            "title": "Attention Is All You Need",
            "abstract": "A transformer architecture.",
            "authors": ["Ashish Vaswani"],
            "published": "2017-06-12",
            "citation_count": 190_373,
            "url_abs": "https://arxiv.org/abs/1706.03762v7",
            "url_pdf": "https://arxiv.org/pdf/1706.03762v7.pdf",
            "tasks": [
                {
                    "id": "6",
                    "name": "Machine Translation",
                    "slug": "machine-translation",
                }
            ],
            "methods": [{"id": "1", "name": "Transformer", "slug": "transformer"}],
            "repositories": [
                {
                    "url": "https://github.com/tensorflow/tensor2tensor",
                    "is_official": True,
                }
            ],
            "project_pages": ["https://example.test/transformer"],
        }

    def read_paper(self, paper: str):
        assert paper == "1706.03762"
        return "abcdefgh"

    def list_papers(self, **_kwargs):
        return self.search_papers()

    def get_related_papers(self, paper: str, *, limit: int):
        assert paper == "1706.03762"
        assert limit == 2
        return self.search_papers()

    def get_paper_lineage(self, paper: str):
        assert paper == "1706.03762"
        return {
            "paper": {
                "id": 755,
                "reference": "1706.03762",
                "title": "Attention Is All You Need",
            },
            "predecessors": [],
            "successors": [
                {"id": 900, "reference": "2001.00001", "title": "A Follow-up"}
            ],
        }

    def get_task(self, task: str):
        assert task == "image-classification"
        return {
            "task": {
                "id": "1",
                "name": "Image Classification",
                "slug": "image-classification",
                "description": "Assign a class to an image.",
                "paper_count": 2343,
            },
            "area": {"id": "1", "name": "Vision"},
            "parents": [],
            "children": [],
            "benchmarks": [
                {
                    "id": "72",
                    "name": "ImageNet-1k",
                    "slug": "imagenet-1k",
                    "paper_count": 124,
                }
            ],
        }

    def get_method(self, method: str):
        assert method == "transformer"
        return {
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

    def list_benchmarks(self, **_kwargs):
        return {
            "next_page": None,
            "results": [
                {
                    "id": "72",
                    "name": "ImageNet-1k",
                    "slug": "imagenet-1k",
                    "paper_count": 124,
                }
            ],
        }

    def get_benchmark(self, benchmark: str, *, limit: int, is_open: bool | None):
        assert benchmark == "imagenet-1k"
        assert limit in {5, 10}
        assert is_open in {True, None}
        return {
            "benchmark": {"id": "72", "name": "ImageNet-1k", "slug": "imagenet-1k"},
            "count": 1,
            "results": [
                {
                    "id": "10",
                    "model_name": "ExampleNet",
                    "metrics": {"Accuracy": 90.1},
                    "best_rank": 1,
                    "paper_id": "755",
                    "paper_title": "Attention Is All You Need",
                    "paper_arxiv_id": "1706.03762",
                    "is_open": True,
                    "num_parameters": 1000,
                }
            ],
        }


def test_search_papers_is_a_read_only_structured_tool():
    async def exercise():
        async with Client(build_server(StubCatalog())) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            result = await client.call_tool(
                "search_papers",
                {"query": "transformer", "mode": "keyword", "limit": 1},
            )
        return tools, result

    tools, result = asyncio.run(exercise())

    assert tools["search_papers"].annotations.read_only_hint is True
    assert tools["search_papers"].input_schema["properties"]["limit"]["maximum"] == 25
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
    }
    assert result.is_error is False


def test_search_rejects_invalid_ranges_before_calling_the_catalog():
    async def exercise():
        async with Client(build_server(StubCatalog())) as client:
            return await client.call_tool(
                "search_papers",
                {
                    "query": "transformer",
                    "published_after": "2026-08-31",
                    "published_before": "2026-08-01",
                },
            )

    result = asyncio.run(exercise())

    assert result.is_error is True
    assert result.content[0].text == (
        "Error executing tool search_papers: "
        "published_after must be on or before published_before"
    )


def test_catalog_failures_do_not_expose_or_log_user_queries(caplog):
    secret_query = "private unreleased project heliotrope"

    class FailingCatalog(StubCatalog):
        def search_papers(self, **_kwargs):
            raise ResponseError(f"Paper title not found: {secret_query}")

    async def exercise():
        async with Client(build_server(FailingCatalog())) as client:
            return await client.call_tool("search_papers", {"query": secret_query})

    with caplog.at_level(logging.INFO):
        result = asyncio.run(exercise())

    assert result.is_error is True
    assert result.content[0].text == (
        "Error executing tool search_papers: "
        "the Papers With Code catalog request failed"
    )
    assert secret_query not in caplog.text


def test_paper_info_and_reading_use_stable_schemas_and_opaque_continuation():
    async def exercise():
        async with Client(build_server(StubCatalog(), read_chunk_chars=5)) as client:
            info = await client.call_tool("get_paper_info", {"paper": "1706.03762"})
            first = await client.call_tool("read_paper", {"paper": "1706.03762"})
            second = await client.call_tool(
                "read_paper",
                {
                    "paper": "1706.03762",
                    "cursor": first.structured_content["next_cursor"],
                },
            )
        return info, first, second

    info, first, second = asyncio.run(exercise())

    assert info.structured_content["paper"]["title"] == "Attention Is All You Need"
    assert info.structured_content["paper"]["tasks"] == [
        {"id": "6", "name": "Machine Translation", "slug": "machine-translation"}
    ]
    assert info.structured_content["paper"]["repositories"][0]["is_official"] is True
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


def test_read_paper_rejects_invalid_continuation_as_an_expected_error():
    async def exercise():
        async with Client(build_server(StubCatalog())) as client:
            return await client.call_tool(
                "read_paper", {"paper": "1706.03762", "cursor": "%%%private%%%"}
            )

    result = asyncio.run(exercise())

    assert result.is_error is True
    assert result.content[0].text == (
        "Error executing tool read_paper: invalid continuation cursor"
    )


def test_paper_listing_related_work_and_lineage_are_composable():
    async def exercise():
        async with Client(build_server(StubCatalog())) as client:
            listed = await client.call_tool(
                "list_papers", {"task": "machine-translation", "limit": 1}
            )
            related = await client.call_tool(
                "get_related_papers", {"paper": "1706.03762", "limit": 2}
            )
            lineage = await client.call_tool(
                "get_paper_lineage", {"paper": "1706.03762"}
            )
        return listed, related, lineage

    listed, related, lineage = asyncio.run(exercise())

    assert listed.structured_content["items"][0]["arxiv_id"] == "1706.03762"
    assert related.structured_content["items"][0]["id"] == "755"
    assert lineage.structured_content["paper"]["reference"] == "1706.03762"
    assert lineage.structured_content["successors"] == [
        {"id": "900", "reference": "2001.00001", "title": "A Follow-up"}
    ]


def test_taxonomy_and_benchmark_tools_return_stable_catalog_entities():
    async def exercise():
        async with Client(build_server(StubCatalog())) as client:
            tool_names = {tool.name for tool in (await client.list_tools()).tools}
            task = await client.call_tool("get_task", {"task": "image-classification"})
            method = await client.call_tool("get_method", {"method": "transformer"})
            benchmarks = await client.call_tool(
                "list_benchmarks", {"task": "image-classification"}
            )
            benchmark = await client.call_tool(
                "get_benchmark",
                {"benchmark": "imagenet-1k", "limit": 5, "is_open": True},
            )
        return tool_names, task, method, benchmarks, benchmark

    tool_names, task, method, benchmarks, benchmark = asyncio.run(exercise())

    assert tool_names == {
        "search_papers",
        "list_papers",
        "get_paper_info",
        "read_paper",
        "get_related_papers",
        "get_paper_lineage",
        "get_task",
        "get_method",
        "list_benchmarks",
        "get_benchmark",
    }
    assert task.structured_content["task"]["area"] == {"id": "1", "name": "Vision"}
    assert method.structured_content["method"]["introduced_year"] == 2017
    assert benchmarks.structured_content["items"][0]["slug"] == "imagenet-1k"
    assert benchmark.structured_content["evaluations"][0]["metrics"] == {
        "Accuracy": 90.1
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
