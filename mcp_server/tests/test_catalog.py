from __future__ import annotations

import json

import pytest
from pwc_cli.transport import Response, ResponseError
from pwc_mcp.catalog import (
    AmbiguousError,
    CatalogClient,
    NotFoundError,
    UpstreamTimeoutError,
)


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

    first = catalog.get_paper_info(
        "https://arxiv.org/pdf/1706.03762v7.pdf", include_resources=True
    )
    second = catalog.get_paper_info(
        "https://huggingface.co/papers/1706.03762", include_resources=True
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

    with pytest.raises(AmbiguousError, match="ambiguous") as captured:
        catalog.get_paper_lineage("Same Title")
    assert [candidate["reference"] for candidate in captured.value.candidates] == [
        "1111.11111",
        "2222.22222",
    ]


def test_catalog_reports_a_typed_missing_title():
    catalog = CatalogClient(transport=StubTransport({"papers/search": {"results": []}}))

    with pytest.raises(NotFoundError, match="Paper title not found"):
        catalog.get_related_papers("A Paper That Does Not Exist", limit=5)


def test_catalog_reports_a_typed_upstream_timeout():
    class TimeoutTransport:
        def get(self, _path, _params=None):
            raise TimeoutError("timed out")

    catalog = CatalogClient(transport=TimeoutTransport())

    with pytest.raises(UpstreamTimeoutError):
        catalog.search_papers(query="transformers")


def test_catalog_rejects_ambiguous_taxonomy_names_with_candidates():
    catalog = CatalogClient(
        transport=StubTransport(
            {
                "datasets/": {
                    "results": [
                        {"id": "1", "name": "ImageNet", "slug": "imagenet-a"},
                        {"id": "2", "name": "ImageNet", "slug": "imagenet-b"},
                    ]
                }
            }
        )
    )

    with pytest.raises(AmbiguousError) as captured:
        catalog.get_benchmark("ImageNet", limit=10, is_open=None)

    assert [candidate["slug"] for candidate in captured.value.candidates] == [
        "imagenet-a",
        "imagenet-b",
    ]


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

    assert catalog.get_paper_info(
        "https://paperswithcode.co/paper/attention-is-all-you-need",
        include_resources=False,
    ) == {"id": "755"}
    assert catalog.get_paper_info("math.GT/0309136", include_resources=False) == {
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
        catalog.get_paper_lineage("Same Title")


def test_catalog_fails_closed_when_paper_filters_are_not_confirmed():
    transport = StubTransport({"papers/": {"results": [], "applied_filters": {}}})
    catalog = CatalogClient(transport=transport)

    with pytest.raises(ResponseError, match="did not confirm"):
        catalog.list_papers(task="image-classification", page=1, limit=10)
