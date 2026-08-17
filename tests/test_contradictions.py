import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.agents import gaps
from app.agents.claude import MissingAPIKeyError
from app.config import settings

# Seeding helpers already exist one file over; a second copy would only rot.
from tests.test_graph_traversal import add_concept, add_paper, link

IMPROVES = "Distillation improves accuracy on every benchmark we tried."
DEGRADES = "Distillation degrades accuracy under distribution shift."


class FakeMessages:
    """Records the request and replays one canned reply per call; an Exception is raised."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply)])


def fake_client(monkeypatch, *replies) -> FakeMessages:
    messages = FakeMessages(replies)
    monkeypatch.setattr(gaps, "_client", lambda: SimpleNamespace(messages=messages))
    return messages


def verdict(verdict="contradicts", papers=(), reasoning="Because they say opposite things.") -> str:
    return json.dumps({"verdict": verdict, "papers": list(papers), "reasoning": reasoning})


def disagreement(session):
    """Two papers, same ordered pair, opposite relations — the signal itself."""
    p1, p2 = add_paper(session, "1", "Paper One"), add_paper(session, "2", "Paper Two")
    a, b = add_concept(session, "Distillation"), add_concept(session, "Accuracy")
    link(session, a, "improves", b, p1, IMPROVES)
    link(session, a, "degrades", b, p2, DEGRADES)
    return p1, p2, a, b


# --- half A: the candidates, no API key required -------------------------------


def test_two_papers_disagreeing_about_a_pair_are_a_candidate(db_session):
    p1, p2, _, _ = disagreement(db_session)

    found = gaps.find_conflicting_claims(db_session)

    assert len(found) == 1
    conflict = found[0]
    assert (conflict.source_concept, conflict.target_concept) == ("Distillation", "Accuracy")
    assert {(c.paper_id, c.relation, c.evidence) for c in conflict.claims} == {
        (p1.id, "improves", IMPROVES),
        (p2.id, "degrades", DEGRADES),
    }


def test_two_papers_asserting_the_same_relation_are_corroboration(db_session):
    """Agreement is the opposite signal. It must not be flagged as a conflict."""
    p1, p2 = add_paper(db_session, "1", "Paper One"), add_paper(db_session, "2", "Paper Two")
    a, b = add_concept(db_session, "Distillation"), add_concept(db_session, "Accuracy")
    link(db_session, a, "improves", b, p1, IMPROVES)
    link(db_session, a, "improves", b, p2, "Distillation improves accuracy, we confirm.")

    assert gaps.find_conflicting_claims(db_session) == []


def test_one_paper_asserting_two_relations_is_not_a_conflict(db_session):
    """That is one paper being verbose, not the literature disagreeing."""
    paper = add_paper(db_session, "1", "Paper One")
    a, b = add_concept(db_session, "Distillation"), add_concept(db_session, "Accuracy")
    link(db_session, a, "improves", b, paper, IMPROVES)
    link(db_session, a, "degrades", b, paper, DEGRADES)

    assert gaps.find_conflicting_claims(db_session) == []


def test_the_reverse_direction_is_a_different_claim_not_a_conflict(db_session):
    """Documented limitation: candidates are keyed on the ordered pair, so `B degrades A`
    never competes with `A improves B` — different claims, and possibly a real
    contradiction this half misses."""
    p1, p2 = add_paper(db_session, "1", "Paper One"), add_paper(db_session, "2", "Paper Two")
    a, b = add_concept(db_session, "Distillation"), add_concept(db_session, "Accuracy")
    link(db_session, a, "improves", b, p1, IMPROVES)
    link(db_session, b, "degrades", a, p2, "Accuracy degrades distillation.")

    assert gaps.find_conflicting_claims(db_session) == []


def test_three_papers_disagreeing_are_one_candidate_carrying_all_three(db_session):
    p1, p2, a, b = disagreement(db_session)
    p3 = add_paper(db_session, "3", "Paper Three")
    link(db_session, a, "has no effect on", b, p3, "Distillation has no effect on accuracy.")

    found = gaps.find_conflicting_claims(db_session)

    assert len(found) == 1
    assert {c.paper_id for c in found[0].claims} == {p1.id, p2.id, p3.id}
    assert {c.relation for c in found[0].claims} == {"improves", "degrades", "has no effect on"}


def test_an_empty_graph_has_no_conflicts(db_session):
    assert gaps.find_conflicting_claims(db_session) == []


def test_limit_is_honoured(db_session):
    disagreement(db_session)
    p1, p2 = add_paper(db_session, "3", "Paper Three"), add_paper(db_session, "4", "Paper Four")
    c, d = add_concept(db_session, "Pruning"), add_concept(db_session, "Latency")
    link(db_session, c, "reduces", d, p1, "Pruning reduces latency.")
    link(db_session, c, "increases", d, p2, "Pruning increases latency.")

    assert len(gaps.find_conflicting_claims(db_session)) == 2
    assert len(gaps.find_conflicting_claims(db_session, limit=1)) == 1


# --- half B: the judge ---------------------------------------------------------


def test_a_contradicting_verdict_is_built_from_the_real_rows(db_session, monkeypatch):
    p1, p2, _, _ = disagreement(db_session)
    candidates = gaps.find_conflicting_claims(db_session)
    messages = fake_client(
        monkeypatch,
        verdict(papers=[p1.id, p2.id], reasoning="One reports a gain, the other a loss."),
    )

    found = gaps.judge_contradictions(candidates)

    assert len(found) == 1
    contradiction = found[0]
    assert contradiction.conflict is candidates[0]
    assert contradiction.reasoning == "One reports a gain, the other a loss."
    assert {(c.paper_id, c.relation, c.evidence) for c in contradiction.claims} == {
        (p1.id, "improves", IMPROVES),
        (p2.id, "degrades", DEGRADES),
    }
    # The judge cannot rule on relation labels alone; it must be shown the evidence.
    prompt = messages.calls[0]["messages"][0]["content"]
    assert IMPROVES in prompt and DEGRADES in prompt


def test_a_compatible_verdict_is_not_a_contradiction(db_session, monkeypatch):
    disagreement(db_session)
    candidates = gaps.find_conflicting_claims(db_session)
    fake_client(monkeypatch, verdict("compatible", reasoning="Different wording, same finding."))

    assert gaps.judge_contradictions(candidates) == []


def test_a_verdict_naming_a_paper_outside_the_candidate_is_dropped(db_session, monkeypatch, caplog):
    """A fabricated row is exactly what must never reach the output."""
    p1, _, _, _ = disagreement(db_session)
    candidates = gaps.find_conflicting_claims(db_session)
    fake_client(monkeypatch, verdict(papers=[p1.id, 9999]))

    assert gaps.judge_contradictions(candidates) == []
    assert "9999" in caplog.text


def test_unparseable_json_skips_one_candidate_and_judges_the_rest(db_session, monkeypatch):
    disagreement(db_session)
    p3, p4 = add_paper(db_session, "3", "Paper Three"), add_paper(db_session, "4", "Paper Four")
    c, d = add_concept(db_session, "Pruning"), add_concept(db_session, "Latency")
    link(db_session, c, "reduces", d, p3, "Pruning reduces latency.")
    link(db_session, c, "increases", d, p4, "Pruning increases latency.")
    candidates = gaps.find_conflicting_claims(db_session)
    fake_client(
        monkeypatch,
        "Sure! These two papers seem to disagree.",
        verdict(papers=[p3.id, p4.id], reasoning="Reduces and increases are opposites."),
    )

    found = gaps.judge_contradictions(candidates)

    assert [c.conflict.source_concept for c in found] == ["Pruning"]


def test_an_api_error_skips_one_candidate_and_judges_the_rest(db_session, monkeypatch):
    disagreement(db_session)
    p3, p4 = add_paper(db_session, "3", "Paper Three"), add_paper(db_session, "4", "Paper Four")
    c, d = add_concept(db_session, "Pruning"), add_concept(db_session, "Latency")
    link(db_session, c, "reduces", d, p3, "Pruning reduces latency.")
    link(db_session, c, "increases", d, p4, "Pruning increases latency.")
    candidates = gaps.find_conflicting_claims(db_session)
    fake_client(
        monkeypatch,
        anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")),
        verdict(papers=[p3.id, p4.id]),
    )

    found = gaps.judge_contradictions(candidates)

    assert [c.conflict.source_concept for c in found] == ["Pruning"]


def test_missing_api_key_still_fails_loud(db_session, monkeypatch):
    """Rules.md reserves hard failure for missing keys — no silent empty verdict list."""
    disagreement(db_session)
    candidates = gaps.find_conflicting_claims(db_session)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")

    with pytest.raises(MissingAPIKeyError):
        gaps.judge_contradictions(candidates)
