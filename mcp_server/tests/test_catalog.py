from __future__ import annotations

import json

import pytest
from pwc_cli.transport import Response, ResponseError
from pwc_mcp.catalog import CatalogClient


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


def test_catalog_rejects_upstream_markdown_truncation():
    class TruncatedTransport:
        def get(self, path, params=None):
            assert path == "research/papers/1706.03762/read"
            return Response(b"partial", {"x-pwc-truncated": "1"})

    catalog = CatalogClient(transport=TruncatedTransport())

    with pytest.raises(ResponseError, match="incomplete Markdown"):
        catalog.read_paper("1706.03762")


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
        catalog.get_paper_lineage("Same Title")


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
