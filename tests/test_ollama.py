"""The Ollama provider: adapter shape, and that its failures still degrade to 503s.

Every call here goes through httpx.MockTransport — no Ollama process is ever contacted.
"""

import json

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.agents import claude
from app.agents.ollama import OllamaClient
from app.agents.orchestrator import Orchestrator
from app.agents.synthesis import synthesize
from app.config import settings
from tests.test_orchestrator import CHUNK, StubWorker

client = TestClient(main.app)

REPLY = {
    "model": "qwen2.5:7b-instruct",
    "created_at": "2026-08-26T10:00:00Z",
    "message": {"role": "assistant", "content": "Attention weights every token [1]."},
    "done": True,
}


def transport(handler):
    """An OllamaClient whose HTTP goes to `handler` instead of a socket."""
    return OllamaClient(http=httpx.Client(transport=httpx.MockTransport(handler)))


def recorder(reply=REPLY, status=200):
    """Handler that records the request it was given and replies with `reply`."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=reply)

    handler.seen = seen
    return handler


def use_ollama(monkeypatch, handler):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setitem(claude._CLIENTS, "ollama", transport(handler))


def ask(monkeypatch):
    """POST /ask with retrieval stubbed but synthesis real, so the call reaches _client()."""
    monkeypatch.setattr(
        main,
        "orchestrator",
        Orchestrator(workers=[StubWorker([CHUNK])], synthesize_fn=synthesize),
    )
    return client.post("/ask", json={"question": "What is attention?"})


# --- 1. the reply is adapted into the block shape the six call sites read ---


def test_reply_becomes_anthropic_shaped_text_blocks():
    message = transport(recorder()).messages.create(
        model="claude-sonnet-5", max_tokens=100, system="S", messages=[{"role": "user", "content": "Q"}]
    )

    assert [block.type for block in message.content] == ["text"]
    # The exact idiom every call site uses.
    assert "".join(b.text for b in message.content if b.type == "text") == REPLY["message"]["content"]


# --- 2. the request Ollama actually receives ---


def test_system_kwarg_max_tokens_and_model_are_mapped(monkeypatch):
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://ollama.test:11434/")
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen2.5:7b-instruct")
    handler = recorder()

    transport(handler).messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=1234,
        system="You are terse.",
        messages=[{"role": "user", "content": "Q"}],
    )

    request = handler.seen[0]
    assert str(request.url) == "http://ollama.test:11434/api/chat"
    body = json.loads(request.content)
    # The Anthropic model the call site passed is discarded: only OLLAMA_MODEL names a
    # model this server has.
    assert body["model"] == "qwen2.5:7b-instruct"
    assert body["model"] != settings.ANTHROPIC_MODEL
    assert body["messages"] == [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "Q"},
    ]
    assert body["stream"] is False
    assert body["options"]["num_predict"] == 1234


# --- 3. the one that matters: an unreachable Ollama is a 503, not a 500 ---


def test_connection_refused_is_a_503_through_ask(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("[Errno 111] Connection refused", request=request)

    use_ollama(monkeypatch, refuse)

    response = ask(monkeypatch)

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "Ollama" in detail
    assert "ollama serve" in detail


def test_connection_refused_raises_an_anthropic_api_error(monkeypatch):
    """The adapter's failures satisfy the `except anthropic.APIError` already in six modules."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")

    def refuse(request):
        raise httpx.ConnectError("nope", request=request)

    with pytest.raises(anthropic.APIError):
        transport(refuse).messages.create(
            model="m", max_tokens=10, system="S", messages=[{"role": "user", "content": "Q"}]
        )


# --- 4. an unpulled model is a 503 that says how to fix it ---


def test_model_not_pulled_is_a_503_telling_you_to_pull_it(monkeypatch):
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen2.5:7b-instruct")
    use_ollama(
        monkeypatch,
        recorder({"error": 'model "qwen2.5:7b-instruct" not found, try pulling it first'}, 404),
    )

    response = ask(monkeypatch)

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "ollama pull qwen2.5:7b-instruct" in detail
    assert "Traceback" not in detail


# --- 5. the default provider is untouched ---


def test_default_provider_is_anthropic_and_still_demands_a_key(monkeypatch):
    assert settings.LLM_PROVIDER == "anthropic"
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(claude, "_CLIENTS", {})

    with pytest.raises(claude.MissingAPIKeyError):
        claude._client()


def test_default_provider_ask_still_returns_the_anthropic_key_503(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(claude, "_CLIENTS", {})

    response = ask(monkeypatch)

    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_anthropic_provider_builds_the_anthropic_client(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(claude, "_CLIENTS", {})

    assert isinstance(claude._client(), anthropic.Anthropic)


# --- 6. a reply we cannot read degrades instead of crashing ---


@pytest.mark.parametrize(
    "reply",
    [{}, {"message": {}}, {"message": {"role": "assistant", "content": ""}}, {"error": "eh"}],
)
def test_shapeless_reply_yields_no_text_rather_than_an_exception(reply):
    message = transport(recorder(reply)).messages.create(
        model="m", max_tokens=10, system="S", messages=[{"role": "user", "content": "Q"}]
    )

    assert "".join(b.text for b in message.content if b.type == "text") == ""


def test_non_json_body_yields_no_text_rather_than_an_exception():
    def html(request):
        return httpx.Response(200, text="<html>not ollama</html>")

    message = transport(html).messages.create(
        model="m", max_tokens=10, system="S", messages=[{"role": "user", "content": "Q"}]
    )

    assert "".join(b.text for b in message.content if b.type == "text") == ""


def test_empty_reply_reaches_ask_as_an_empty_answer_not_a_500(monkeypatch):
    use_ollama(monkeypatch, recorder({"message": {"role": "assistant", "content": ""}}))

    response = ask(monkeypatch)

    assert response.status_code == 200
    assert response.json()["answer"] == ""
