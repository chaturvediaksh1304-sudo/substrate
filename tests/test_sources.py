import httpx
import pytest

from app.ingestion.sources import fetch_arxiv, fetch_semantic_scholar

S2_BODY = {
    "total": 2,
    "offset": 0,
    "data": [
        {
            "paperId": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
            "title": "Attention is All you Need",
            "abstract": "The dominant sequence transduction models are based on complex "
            "recurrent or convolutional neural networks.",
            "year": 2017,
            "url": "https://www.semanticscholar.org/paper/204e3073870fae3d05bcbc2f6a8e263d9b72e776",
            "authors": [
                {"authorId": "40348417", "name": "Ashish Vaswani"},
                {"authorId": "1846258", "name": "Noam M. Shazeer"},
            ],
        },
        {
            "paperId": "df2b0e26d0599ce3e70df8a9da02e51594e0e992",
            "title": "BERT: Pre-training of Deep Bidirectional Transformers",
            "abstract": "We introduce a new language representation model called BERT.",
            "year": 2019,
            "url": "https://www.semanticscholar.org/paper/df2b0e26d0599ce3e70df8a9da02e51594e0e992",
            "authors": [{"authorId": "39172707", "name": "Jacob Devlin"}],
        },
    ],
}

ARXIV_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <link href="http://arxiv.org/api/query?search_query%3Dall:transformers" rel="self" type="application/atom+xml"/>
  <title type="html">ArXiv Query: search_query=all:transformers&amp;start=0&amp;max_results=2</title>
  <id>http://arxiv.org/api/QsBBvSyLQZbEFYDzD3ZuS8Nspxc</id>
  <updated>2024-01-09T00:00:00-05:00</updated>
  <opensearch:totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">2</opensearch:totalResults>
  <opensearch:startIndex xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">0</opensearch:startIndex>
  <opensearch:itemsPerPage xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">2</opensearch:itemsPerPage>
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <updated>2023-08-02T00:41:18Z</updated>
    <published>2017-06-12T17:57:34Z</published>
    <title>Attention Is All You
  Need</title>
    <summary>  The dominant sequence transduction models are based on complex recurrent or
convolutional neural networks.
</summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <arxiv:comment xmlns:arxiv="http://arxiv.org/schemas/atom">15 pages, 5 figures</arxiv:comment>
    <link href="http://arxiv.org/abs/1706.03762v7" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/1706.03762v7" rel="related" type="application/pdf"/>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.CL"
      scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/1810.04805v2</id>
    <updated>2019-05-24T20:37:26Z</updated>
    <published>2018-10-11T00:50:01Z</published>
    <title>BERT: Pre-training of Deep Bidirectional Transformers</title>
    <summary>  We introduce a new language representation model called BERT.
</summary>
    <author><name>Jacob Devlin</name></author>
    <link href="http://arxiv.org/abs/1810.04805v2" rel="alternate" type="text/html"/>
    <category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
"""


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def responder(response):
    def handler(request):
        return response

    return handler


def raiser(exc):
    def handler(request):
        raise exc

    return handler


# --- Semantic Scholar ---------------------------------------------------------


def test_s2_happy_path():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        seen["headers"] = request.headers
        return httpx.Response(200, json=S2_BODY)

    papers = fetch_semantic_scholar("transformers", 2, client=client_for(handler))

    assert seen["url"].params["query"] == "transformers"
    assert seen["url"].params["limit"] == "2"
    assert "abstract" in seen["url"].params["fields"]
    assert "x-api-key" not in seen["headers"]  # unset key must not be sent

    assert len(papers) == 2
    first = papers[0]
    assert first.source == "semantic_scholar"
    assert first.external_id == "204e3073870fae3d05bcbc2f6a8e263d9b72e776"
    assert first.title == "Attention is All you Need"
    assert first.abstract.startswith("The dominant sequence transduction models")
    assert first.authors == ["Ashish Vaswani", "Noam M. Shazeer"]
    assert first.year == 2017
    assert first.url.endswith("204e3073870fae3d05bcbc2f6a8e263d9b72e776")


def test_s2_timeout_returns_empty():
    papers = fetch_semantic_scholar(
        "transformers", 2, client=client_for(raiser(httpx.TimeoutException("timed out")))
    )
    assert papers == []


def test_s2_rate_limited_returns_empty():
    papers = fetch_semantic_scholar(
        "transformers", 2, client=client_for(responder(httpx.Response(429, text="Too Many Requests")))
    )
    assert papers == []


def test_s2_malformed_json_returns_empty():
    papers = fetch_semantic_scholar(
        "transformers", 2, client=client_for(responder(httpx.Response(200, text="<html>nope</html>")))
    )
    assert papers == []


def test_s2_skips_paper_without_abstract():
    body = {
        "data": [
            S2_BODY["data"][0],
            {
                "paperId": "abstractless",
                "title": "Paper With No Abstract",
                "abstract": None,
                "year": 2020,
                "url": "https://example.org/abstractless",
                "authors": [{"authorId": "1", "name": "Nobody"}],
            },
            S2_BODY["data"][1],
        ]
    }
    papers = fetch_semantic_scholar("transformers", 3, client=client_for(responder(httpx.Response(200, json=body))))

    assert [p.external_id for p in papers] == [
        "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
        "df2b0e26d0599ce3e70df8a9da02e51594e0e992",
    ]


# --- arXiv --------------------------------------------------------------------


def test_arxiv_happy_path():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return httpx.Response(200, text=ARXIV_BODY)

    papers = fetch_arxiv("transformers", 2, client=client_for(handler))

    assert seen["url"].params["search_query"] == "all:transformers"
    assert seen["url"].params["max_results"] == "2"

    assert len(papers) == 2
    first = papers[0]
    assert first.source == "arxiv"
    assert first.external_id == "1706.03762v7"
    assert first.title == "Attention Is All You Need"
    assert first.abstract.startswith("The dominant sequence transduction models")
    assert "\n" not in first.abstract
    assert first.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert first.year == 2017
    assert first.url == "http://arxiv.org/abs/1706.03762v7"


def test_arxiv_timeout_returns_empty():
    papers = fetch_arxiv("transformers", 2, client=client_for(raiser(httpx.TimeoutException("timed out"))))
    assert papers == []


def test_arxiv_rate_limited_returns_empty():
    papers = fetch_arxiv("transformers", 2, client=client_for(responder(httpx.Response(429, text="slow down"))))
    assert papers == []


def test_arxiv_malformed_xml_returns_empty():
    papers = fetch_arxiv(
        "transformers", 2, client=client_for(responder(httpx.Response(200, text="<feed><entry></feed>")))
    )
    assert papers == []


def test_arxiv_skips_entry_without_abstract():
    body = ARXIV_BODY.replace(
        "<summary>  We introduce a new language representation model called BERT.\n</summary>",
        "<summary>   </summary>",
    )
    papers = fetch_arxiv("transformers", 2, client=client_for(responder(httpx.Response(200, text=body))))

    assert [p.external_id for p in papers] == ["1706.03762v7"]


@pytest.mark.parametrize("fetch", [fetch_semantic_scholar, fetch_arxiv])
def test_connect_error_returns_empty(fetch):
    handler = raiser(httpx.ConnectError("no route to host"))
    assert fetch("transformers", 2, client=client_for(handler)) == []
