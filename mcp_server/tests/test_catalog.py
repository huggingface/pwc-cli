from __future__ import annotations

import json

import pytest
from pwc_cli.cli import UsageError
from pwc_cli.transport import Response, ResponseError
from pwc_mcp.catalog import CatalogClient, cache_ttl


class StubTransport:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        value = self.routes[path]
        if isinstance(value, str):
            return Response(value.encode(), {"content-type": "text/markdown"})
        return Response(
            json.dumps(value).encode(), {"content-type": "application/json"}
        )


def test_catalog_normalizes_paper_urls_and_caches_identical_reads():
    transport = StubTransport(
        {
            "papers/1706.03762": {"id": "755", "title": "Attention Is All You Need"},
        }
    )
    catalog = CatalogClient(transport=transport)

    first = catalog.query(
        ("paper", "info"),
        {"paper": "https://arxiv.org/pdf/1706.03762v7.pdf", "include_resources": True},
    )
    second = catalog.query(
        ("paper", "info"),
        {
            "paper": "https://huggingface.co/papers/1706.03762",
            "include_resources": True,
        },
    )

    assert first == second
    assert transport.calls == [("papers/1706.03762", {"include_resources": True})]


def test_catalog_reads_one_version_bound_markdown_chunk_per_call():
    version = "a" * 64

    class ChunkTransport:
        def __init__(self):
            self.calls = []

        def get(self, path, params=None):
            self.calls.append((path, params))
            if params["offset"] == 0:
                return Response(
                    "α first".encode(),
                    {
                        "x-pwc-truncated": "1",
                        "x-pwc-content-version": version,
                        "x-pwc-next-offset": "8",
                    },
                )
            return Response(
                b" terminal",
                {
                    "x-pwc-truncated": "0",
                    "x-pwc-content-version": version,
                },
            )

    transport = ChunkTransport()
    catalog = CatalogClient(transport=transport)
    first = catalog.read_paper_chunk("1706.03762", limit=65_536)
    second = catalog.read_paper_chunk(
        "1706.03762",
        offset=first.next_offset,
        content_version=first.content_version,
        limit=65_536,
        resolved=True,
    )

    assert first.markdown == "α first"
    assert first.next_offset == 8
    assert second.markdown == " terminal"
    assert second.next_offset is None
    assert transport.calls == [
        (
            "research/papers/1706.03762/read",
            {"offset": 0, "limit": 65_536, "content_version": None},
        ),
        (
            "research/papers/1706.03762/read",
            {"offset": 8, "limit": 65_536, "content_version": version},
        ),
    ]


def test_catalog_caches_versioned_chunks_and_rejects_nonadvancing_offsets():
    version = "b" * 64

    class ChunkTransport:
        def __init__(self):
            self.calls = 0

        def get(self, _path, _params=None):
            self.calls += 1
            return Response(
                b"data",
                {
                    "x-pwc-truncated": "1",
                    "x-pwc-content-version": version,
                    "x-pwc-next-offset": "4",
                },
            )

    transport = ChunkTransport()
    catalog = CatalogClient(transport=transport)
    with pytest.raises(ResponseError, match="did not advance"):
        catalog.read_paper_chunk(
            "1706.03762",
            offset=4,
            content_version=version,
            resolved=True,
        )
    assert transport.calls == 1


def test_catalog_versioned_chunk_cache_avoids_repeated_upstream_reads():
    version = "c" * 64

    class ChunkTransport:
        def __init__(self):
            self.calls = 0

        def get(self, _path, _params=None):
            self.calls += 1
            return Response(
                b"done",
                {
                    "x-pwc-truncated": "0",
                    "x-pwc-content-version": version,
                },
            )

    transport = ChunkTransport()
    catalog = CatalogClient(transport=transport)
    first = catalog.read_paper_chunk(
        "1706.03762",
        offset=8,
        content_version=version,
        resolved=True,
    )
    second = catalog.read_paper_chunk(
        "1706.03762",
        offset=8,
        content_version=version,
        resolved=True,
    )

    assert first == second
    assert transport.calls == 1


def test_catalog_resolves_exact_titles_and_rejects_ambiguous_titles():
    transport = StubTransport(
        {
            "papers/search": {
                "results": [
                    {"id": "1", "arxiv_id": "1111.11111", "title": "Same Title"},
                    {"id": "2", "arxiv_id": "2222.22222", "title": "Same Title"},
                ]
            }
        }
    )
    catalog = CatalogClient(transport=transport)

    with pytest.raises(ResponseError, match="ambiguous"):
        catalog.query(("paper", "lineage", "list"), {"paper": "Same Title"})


def test_catalog_resolves_pwc_urls_and_dotted_legacy_arxiv_ids():
    transport = StubTransport(
        {
            "papers/search": {
                "results": [
                    {
                        "id": "755",
                        "arxiv_id": "1706.03762",
                        "title": "Attention Is All You Need",
                    }
                ]
            },
            "papers/1706.03762": {"id": "755"},
            "papers/math.GT%2F0309136": {"id": "900"},
        }
    )
    catalog = CatalogClient(transport=transport)

    assert catalog.query(
        ("paper", "info"),
        {"paper": "https://paperswithcode.co/paper/attention-is-all-you-need"},
    ) == {"id": "755"}
    assert catalog.query(("paper", "info"), {"paper": "math.GT/0309136"}) == {
        "id": "900"
    }


def test_catalog_checks_later_search_pages_for_ambiguous_exact_titles():
    class PaginatedTransport:
        def get(self, path, params=None):
            assert path == "papers/search"
            page = params["page"]
            payload = {
                "results": [
                    {
                        "id": str(page),
                        "arxiv_id": f"1111.1111{page}",
                        "title": "Same Title",
                    }
                ],
                "next_page": 2 if page == 1 else None,
            }
            return Response(json.dumps(payload).encode(), {})

    catalog = CatalogClient(transport=PaginatedTransport())

    with pytest.raises(ResponseError, match="ambiguous"):
        catalog.query(("paper", "lineage", "list"), {"paper": "Same Title"})


def test_catalog_fails_closed_when_paper_filters_are_not_confirmed():
    transport = StubTransport({"papers/": {"results": [], "applied_filters": {}}})
    catalog = CatalogClient(transport=transport)

    with pytest.raises(ResponseError, match="did not confirm"):
        catalog.query(("paper", "list"), {"task": "image-classification"})


def test_catalog_query_runs_cli_commands_with_per_path_cache_lifetimes():
    transport = StubTransport(
        {
            "datasets/": {"results": [{"id": "72", "name": "ImageNet-1k"}]},
            "datasets/72/evaluations/": {
                "count": 1,
                "results": [{"id": "1", "model_name": "A", "metrics": {"top1": 80}}],
            },
        }
    )
    catalog = CatalogClient(transport=transport)

    first = catalog.query(("benchmark",), {"name": "ImageNet-1k", "limit": 5})
    second = catalog.query(("benchmark",), {"name": "imagenet-1k", "limit": 5})

    assert first == second
    assert first["results"][0]["model_name"] == "A"
    assert first["count"] == 1
    # The second lookup differs only in the search text; the identical
    # evaluation request is served from the shared cache.
    assert [path for path, _params in transport.calls] == [
        "datasets/",
        "datasets/72/evaluations/",
        "datasets/",
    ]
    assert cache_ttl("papers/search") == 60
    assert cache_ttl("tasks/1/page") == 600
    assert cache_ttl("datasets/72/evaluations/") == 300


def test_catalog_query_reports_cli_usage_errors_without_upstream_calls():
    transport = StubTransport({})
    catalog = CatalogClient(transport=transport)

    with pytest.raises(UsageError, match="--limit must be 1-100"):
        catalog.query(("search",), {"query": "x", "limit": 0})
    with pytest.raises(UsageError, match="not a read-only"):
        catalog.query(("skills", "add"), {})
    assert transport.calls == []
