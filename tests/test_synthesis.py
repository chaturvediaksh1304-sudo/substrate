from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.agents import synthesis
from app.agents.retrieval import RetrievedChunk
from app.config import settings

ATTENTION = RetrievedChunk(
    text="The Transformer replaces recurrence with attention.",
    score=0.1,
    paper_id=1,
    title="Attention Is All You Need",
    authors=["Ashish Vaswani", "Noam Shazeer"],
    year=2017,
    url="http://arxiv.org/abs/1706.03762v7",
    source="arxiv",
    external_id="1706.03762v7",
)
BERT = RetrievedChunk(
    text="BERT pre-trains deep bidirectional representations.",
    score=0.2,
    paper_id=2,
    title="BERT",
    authors=["Jacob Devlin"],
    year=2018,
    url="http://arxiv.org/abs/1810.04805",
    source="arxiv",
    external_id="1810.04805",
)


class FakeMessages:
    """Records the request and replays a canned reply — or raises, for the error paths."""

    def __init__(self, reply, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)])


def fake_client(monkeypatch, reply="", error=None) -> FakeMessages:
    messages = FakeMessages(reply, error)
    monkeypatch.setattr(synthesis, "_client", lambda: SimpleNamespace(messages=messages))
    return messages


# --- the prompt carries the sources we built, not sources the model imagines ---


def test_prompt_lists_the_retrieved_sources_by_number(monkeypatch):
    messages = fake_client(monkeypatch, reply="An answer [1].")

    synthesis.synthesize("What is attention?", [ATTENTION, BERT])

    prompt = messages.calls[0]["messages"][0]["content"]
    assert "[1] Attention Is All You Need (Ashish Vaswani, Noam Shazeer, 2017)" in prompt
    assert "[2] BERT (Jacob Devlin, 2018)" in prompt
    assert ATTENTION.text in prompt
    assert messages.calls[0]["model"] == settings.ANTHROPIC_MODEL


# --- citation integrity ---


def test_citations_are_built_from_the_chunks_not_the_models_prose(monkeypatch):
    """The model names a fake paper in prose; the citation still comes from the real chunk."""
    fake_client(monkeypatch, reply="Attention beats recurrence [1] (see Smith et al., 1999).")

    result = synthesis.synthesize("What is attention?", [ATTENTION, BERT])

    assert [c.index for c in result.citations] == [1]
    cited = result.citations[0]
    assert cited.title == "Attention Is All You Need"
    assert cited.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert cited.year == 2017
    assert cited.url == "http://arxiv.org/abs/1706.03762v7"
    assert cited.external_id == "1706.03762v7"


def test_only_cited_sources_are_returned(monkeypatch):
    fake_client(monkeypatch, reply="Only the second one is relevant [2].")

    result = synthesis.synthesize("q", [ATTENTION, BERT])

    assert [c.title for c in result.citations] == ["BERT"]


def test_out_of_range_index_is_dropped_not_fabricated(monkeypatch):
    """[7] has no chunk behind it — inventing an entry for it is the failure we hard-fail on."""
    fake_client(monkeypatch, reply="Claim one [1]. Claim two [7]. Claim three [0].")

    result = synthesis.synthesize("q", [ATTENTION, BERT])

    assert [c.index for c in result.citations] == [1]


def test_chunks_from_one_paper_are_a_single_numbered_source(monkeypatch):
    second_chunk = RetrievedChunk(**{**vars(ATTENTION), "text": "Multi-head attention."})
    messages = fake_client(monkeypatch, reply="Answer [1][2].")

    result = synthesis.synthesize("q", [ATTENTION, second_chunk, BERT])

    prompt = messages.calls[0]["messages"][0]["content"]
    assert "[3]" not in prompt
    assert "Multi-head attention." in prompt
    assert [c.title for c in result.citations] == ["Attention Is All You Need", "BERT"]


# --- hard failures: a non-answer must never look like an answer ---


def test_missing_api_key_raises_named_exception(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")

    with pytest.raises(synthesis.MissingAPIKeyError):
        synthesis.synthesize("q", [ATTENTION])


def test_sdk_error_propagates_instead_of_becoming_a_fake_answer(monkeypatch):
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    fake_client(monkeypatch, error=error)

    with pytest.raises(anthropic.APIError):
        synthesis.synthesize("q", [ATTENTION])
