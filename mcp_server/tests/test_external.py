from __future__ import annotations

import json

import pytest
from pwc_mcp.external import (
    PublicPaperLookup,
    arxiv_reference,
    doi_reference,
    title_matches,
)

ARXIV_ABS = b"""<html><head>
<meta name="citation_title" content="Revisiting the scotogenic model with scalar dark matter" />
<meta name="citation_author" content="&#193;vila, Ivania M." />
<meta name="citation_author" content="Cottin, Giovanna" />
<meta name="citation_doi" content="10.1088/1361-6471/ac5fb4" />
<meta name="citation_date" content="2021/08/11" />
<meta name="citation_abstract" content="The scotogenic model. Code: https://github.com/example/scoto." />
</head></html>"""
HF_PAPER = {
    "id": "2410.10122",
    "title": "MuseTalk: Real-Time High-Fidelity Video Dubbing\n  via Spatio-Temporal Sampling",
    "authors": [{"name": "Yue Zhang"}, {"name": "Minhao Liu"}],
    "summary": "Lip   sync.",
    "publishedAt": "2024-10-14T00:00:00.000Z",
    "githubRepo": "https://github.com/TMElyralab/MuseTalk",
    "projectPage": "https://example.test/musetalk",
}
ARXIV_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1803.04189v3</id>
    <published>2018-03-12T10:00:00Z</published>
    <title>Noise2Noise: Learning Image Restoration without Clean Data</title>
    <summary>We apply basic statistical reasoning.</summary>
    <author><name>Jaakko Lehtinen</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2104.03838v1</id>
    <title>Speech Denoising Without Clean Training Data: A Noise2Noise Approach</title>
    <summary>Speech.</summary>
  </entry>
</feed>"""


class StubLookup(PublicPaperLookup):
    def __init__(self, routes):
        self.routes = routes
        self.calls: list[str] = []

    def _get(self, url):
        self.calls.append(url)
        for prefix, body in self.routes.items():
            if url.startswith(prefix):
                return body if isinstance(body, bytes) else json.dumps(body).encode()
        return None


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("2108.05103", "2108.05103"),
        ("2108.05103v2", "2108.05103"),
        ("arXiv:2108.05103", "2108.05103"),
        ("https://arxiv.org/abs/2108.05103v1", "2108.05103"),
        ("https://arxiv.org/pdf/2108.05103.pdf", "2108.05103"),
        ("https://huggingface.co/papers/2410.10122", "2410.10122"),
        ("10.48550/arXiv.2410.10122", "2410.10122"),
        ("https://doi.org/10.48550/arXiv.2410.10122", "2410.10122"),
        ("hep-th/9901001", "hep-th/9901001"),
        ("Noise2Noise", None),
        ("https://example.test/paper", None),
    ],
)
def test_arxiv_reference_recognizes_ids_urls_and_arxiv_dois(reference, expected):
    assert arxiv_reference(reference) == expected


def test_doi_reference_accepts_bare_prefixed_and_resolver_urls():
    assert doi_reference("10.1088/1361-6471/ac5fb4") == "10.1088/1361-6471/ac5fb4"
    assert doi_reference("doi:10.1145/3292500") == "10.1145/3292500"
    assert doi_reference("https://doi.org/10.1145/3292500") == "10.1145/3292500"
    assert doi_reference("https://example.test/10.1145/3292500") is None
    assert doi_reference("Attention Is All You Need") is None


def test_title_matches_full_titles_and_short_names_only():
    title = "Noise2Noise: Learning Image Restoration without Clean Data"
    assert title_matches("Noise2Noise", title)
    assert title_matches("noise2noise: learning image restoration without clean data", title)
    assert not title_matches("Noise2", title)
    assert not title_matches("Noise2Noise", "Speech Denoising: A Noise2Noise Approach")


def test_arxiv_id_prefers_hugging_face_papers_metadata():
    lookup = StubLookup({"https://huggingface.co/api/papers/2410.10122": HF_PAPER})

    record = lookup.lookup("https://arxiv.org/abs/2410.10122v2")

    assert record == {
        "id": "2410.10122",
        "arxiv_id": "2410.10122",
        "doi": None,
        "title": "MuseTalk: Real-Time High-Fidelity Video Dubbing via Spatio-Temporal Sampling",
        "authors": ["Yue Zhang", "Minhao Liu"],
        "abstract": "Lip sync.",
        "published": "2024-10-14",
        "url": "https://arxiv.org/abs/2410.10122",
        "pdf_url": "https://arxiv.org/pdf/2410.10122",
        "hf_url": "https://huggingface.co/papers/2410.10122",
        "repositories": ["https://github.com/TMElyralab/MuseTalk"],
        "project_pages": ["https://example.test/musetalk"],
        "metadata_source": "huggingface",
    }


def test_arxiv_id_falls_back_to_the_arxiv_abstract_page():
    lookup = StubLookup({"https://arxiv.org/abs/2108.05103": ARXIV_ABS})

    record = lookup.lookup("2108.05103")

    assert lookup.calls == [
        "https://huggingface.co/api/papers/2108.05103",
        "https://arxiv.org/abs/2108.05103",
    ]
    assert record is not None
    assert record["title"] == "Revisiting the scotogenic model with scalar dark matter"
    assert record["authors"] == ["Ivania M. Ávila", "Giovanna Cottin"]
    assert record["published"] == "2021-08-11"
    assert record["doi"] == "10.1088/1361-6471/ac5fb4"
    assert record["repositories"] == ["https://github.com/example/scoto"]
    assert record["metadata_source"] == "arxiv"


def test_title_lookup_needs_a_unique_title_or_short_name_match():
    lookup = StubLookup(
        {
            "https://huggingface.co/api/papers/search": [
                {"paper": {"id": "2104.03838", "title": "Speech Denoising: A Noise2Noise Approach"}}
            ],
            "https://export.arxiv.org/api/query": ARXIV_FEED,
        }
    )

    record = lookup.lookup("Noise2Noise")

    assert record is not None
    assert record["arxiv_id"] == "1803.04189"
    assert record["published"] == "2018-03-12"
    assert record["authors"] == ["Jaakko Lehtinen"]
    assert StubLookup({}).lookup("Noise2Noise") is None


def test_doi_lookup_uses_crossref_metadata():
    lookup = StubLookup(
        {
            "https://api.crossref.org/works/10.1088/1361-6471/ac5fb4": {
                "message": {
                    "title": ["Revisiting the scotogenic model"],
                    "author": [{"given": "Giovanna", "family": "Cottin"}],
                    "issued": {"date-parts": [[2022, 4, 20]]},
                    "URL": "https://doi.org/10.1088/1361-6471/ac5fb4",
                    "abstract": "<jats:title>Abstract</jats:title><jats:p>The model.</jats:p>",
                }
            }
        }
    )

    record = lookup.lookup("https://doi.org/10.1088/1361-6471/ac5fb4")

    assert record is not None
    assert record["title"] == "Revisiting the scotogenic model"
    assert record["authors"] == ["Giovanna Cottin"]
    assert record["published"] == "2022-04-20"
    assert record["abstract"] == "The model."
    assert record["metadata_source"] == "crossref"


def test_unsupported_urls_are_not_looked_up():
    lookup = StubLookup({})
    assert lookup.lookup("https://example.test/paper.pdf") is None
    assert lookup.calls == []
