import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.agents.orchestrator import Orchestrator
from app.agents.synthesis import MissingAPIKeyError
from app.config import settings
from tests.test_orchestrator import CHUNK, CITATION, StubWorker, StubSynthesis

client = TestClient(main.app)


def install(monkeypatch, chunks=(CHUNK,), synthesize=None):
    """Swap in an orchestrator whose retrieval and synthesis are both stubbed."""
    orchestrator = Orchestrator(
        workers=[StubWorker(chunks)], synthesize_fn=synthesize or StubSynthesis()
    )
    monkeypatch.setattr(main, "orchestrator", orchestrator)
    return orchestrator


def raising(exc):
    def synthesize(question, chunks):
        raise exc

    return synthesize


# --- 200s ---


def test_valid_question_returns_answer_and_citations(monkeypatch):
    install(monkeypatch)

    response = client.post("/ask", json={"question": "What is attention?", "k": 3})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Synthesized."
    assert body["found"] is True
    assert body["citations"] == [
        {
            "index": 1,
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani"],
            "year": 2017,
            "url": "http://arxiv.org/abs/1706.03762v7",
            "external_id": "1706.03762v7",
        }
    ]


def test_no_results_is_a_200_not_an_error(monkeypatch):
    """"Nothing found" is a legitimate answer to the question, not a failure."""
    install(monkeypatch, chunks=())

    response = client.post("/ask", json={"question": "What is attention?"})

    assert response.status_code == 200
    body = response.json()
    assert body["found"] is False
    assert body["citations"] == []
    assert "no relevant" in body["answer"].lower()


# --- 503s: an unavailable upstream must never look like an empty answer ---


def test_missing_api_key_returns_503(monkeypatch):
    install(monkeypatch, synthesize=raising(MissingAPIKeyError("ANTHROPIC_API_KEY is not set")))

    response = client.post("/ask", json={"question": "What is attention?"})

    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_upstream_anthropic_failure_returns_503(monkeypatch):
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    install(monkeypatch, synthesize=raising(error))

    response = client.post("/ask", json={"question": "What is attention?"})

    assert response.status_code == 503
    assert "claude" in response.json()["detail"].lower()


# --- request validation ---


def test_question_is_required():
    assert client.post("/ask", json={"k": 3}).status_code == 422


def test_empty_question_is_rejected():
    assert client.post("/ask", json={"question": "  "}).status_code == 422


@pytest.mark.parametrize("k", [0, 21])
def test_k_out_of_bounds_is_rejected(k):
    assert client.post("/ask", json={"question": "q", "k": k}).status_code == 422


# --- Phase 2 criterion 5: real question -> real ingested papers -> cited answer ---


@pytest.mark.skipif(not settings.ANTHROPIC_API_KEY, reason="ANTHROPIC_API_KEY is not set")
def test_live_end_to_end_against_the_ingested_corpus():
    """No stubs: real embeddings, real pgvector search, real Claude call."""
    response = client.post(
        "/ask", json={"question": "What do these papers say about attention mechanisms?", "k": 5}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert len(body["answer"]) > 100
    assert body["citations"], "a grounded answer must cite at least one retrieved paper"
    for citation in body["citations"]:
        assert citation["title"]
        assert f"[{citation['index']}]" in body["answer"]
