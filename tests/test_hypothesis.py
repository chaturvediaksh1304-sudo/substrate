import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.agents import gaps, hypothesis
from app.agents.claude import MissingAPIKeyError
from app.config import settings

# The fake Anthropic client and the graph-seeding helpers already exist one file over;
# a second copy of either would only rot.
from tests.test_contradictions import FakeMessages
from tests.test_graph_traversal import add_concept, add_paper, link

RAG_ABSTRACT = (
    "We augment a language model with a dense retriever over Wikipedia and report "
    "exact-match gains on open-domain question answering."
)
HALLUCINATION_ABSTRACT = (
    "We annotate model outputs for unsupported statements and find that hallucination "
    "rises sharply on long-tail entities."
)
DISTILL_UP_ABSTRACT = "Distilled students match their teachers on every benchmark we tried."
DISTILL_DOWN_ABSTRACT = "Distilled students collapse once the evaluation distribution moves."

RA_LM = "Retrieval augmentation improves language models."
LM_HALLUCINATES = "Language models exhibit hallucination."
RA_NEEDS_INDEX = "Retrieval augmentation relies on a vector index."
IMPROVES = "Distillation improves accuracy on every benchmark we tried."
DEGRADES = "Distillation degrades accuracy under distribution shift."


def fake_client(monkeypatch, *replies) -> FakeMessages:
    messages = FakeMessages(replies)
    monkeypatch.setattr(hypothesis, "_client", lambda: SimpleNamespace(messages=messages))
    return messages


def paper(session, external_id, title, abstract):
    row = add_paper(session, external_id, title)
    row.abstract = abstract
    session.commit()
    return row


def proposal(drop=(), **overrides) -> str:
    payload = {
        "statement": (
            "Retrieval augmentation lowers the unsupported-statement rate on long-tail entity "
            "queries, but raises it whenever the retriever returns off-topic passages."
        ),
        "manipulation": (
            "Whether the model answers with or without a dense retriever, crossed with how "
            "precise that retriever is."
        ),
        "measurement": "Unsupported statements per answer, annotated against the passages shown.",
        "predicted_effect": (
            "About a third fewer unsupported statements under a precise retriever, and more "
            "than the no-retrieval baseline under an imprecise one."
        ),
        "falsifier": (
            "The unsupported-statement rate stays flat across every retriever precision level, "
            "or falls as precision falls."
        ),
        "paper_ids": [],
        **overrides,
    }
    return json.dumps({k: v for k, v in payload.items() if k not in drop})


def missing_link(session):
    """Retrieval augmentation and hallucination both tie to language models, never to each other."""
    rag = paper(session, "1", "Retrieval Augmented Generation", RAG_ABSTRACT)
    hall = paper(session, "2", "Measuring Hallucination", HALLUCINATION_ABSTRACT)
    ra = add_concept(session, "Retrieval Augmentation")
    lm = add_concept(session, "Language Models")
    hl = add_concept(session, "Hallucination")
    vi = add_concept(session, "Vector Index")
    link(session, ra, "improves", lm, rag, RA_LM)
    link(session, lm, "exhibits", hl, hall, LM_HALLUCINATES)
    link(session, ra, "relies on", vi, rag, RA_NEEDS_INDEX)
    return _gap(session, "missing_link", ra.id, hl.id), (rag, hall)


def contradiction(session):
    """Two papers, same ordered pair, opposite relations."""
    pro = paper(session, "1", "Distillation Improves Accuracy", DISTILL_UP_ABSTRACT)
    con = paper(session, "2", "Distillation Degrades Accuracy", DISTILL_DOWN_ABSTRACT)
    d, a = add_concept(session, "Distillation"), add_concept(session, "Accuracy")
    link(session, d, "improves", a, pro, IMPROVES)
    link(session, d, "degrades", a, con, DEGRADES)
    return _gap(session, "contradiction", d.id, a.id), (pro, con)


def _gap(session, kind, a_id, b_id):
    """A real CandidateGap off the real rows — the assessment a rank_gaps run would have left."""
    gap = next(
        g
        for g in gaps._candidates(session)
        if g.kind == kind and (g.concept_a_id, g.concept_b_id) == (a_id, b_id)
    )
    gap.significance = 2
    gap.rationale = f"Nobody in this corpus has connected {gap.concept_a} to {gap.concept_b}."
    return gap


# --- the restatement guard: criterion 2's machine-checkable half, no model, no key ----

GAP_TEXT = (
    "Retrieval Augmentation Hallucination "
    "Nobody in this corpus has connected retrieval augmentation to hallucination directly."
)

RESTATEMENTS = [
    "There may be an unexplored relationship between retrieval augmentation and hallucination.",
    "Retrieval augmentation and hallucination are connected in ways the literature has not "
    "yet explored, and this warrants further research.",
    "The link between retrieval augmentation and hallucination is an important open question.",
    "Nobody has connected retrieval augmentation to hallucination, so somebody should.",
    "Retrieval augmentation may affect hallucination.",
]

HYPOTHESES = [
    "Retrieval augmentation reduces hallucination rate on in-domain queries but increases it "
    "on out-of-domain queries.",
    "Retrieval augmentation lowers the unsupported-statement rate on long-tail entity queries, "
    "but raises it whenever the retriever returns off-topic passages.",
    "Hallucination rate falls monotonically as retrieved passage precision rises above 0.6 and "
    "is unchanged below it.",
    "Adding retrieval cuts fabricated citations by half while leaving fabricated dates "
    "untouched.",
]


@pytest.mark.parametrize("statement", RESTATEMENTS)
def test_a_vague_restatement_of_the_gap_is_caught(statement):
    assert hypothesis.restates_gap(statement, GAP_TEXT)


@pytest.mark.parametrize("statement", HYPOTHESES)
def test_a_specific_falsifiable_claim_is_not_a_restatement(statement):
    assert not hypothesis.restates_gap(statement, GAP_TEXT)


def test_an_empty_statement_is_a_restatement():
    assert hypothesis.restates_gap("", GAP_TEXT)


# --- the happy path ------------------------------------------------------------


def test_a_gap_becomes_a_hypothesis_with_every_field_filled(db_session, monkeypatch):
    gap, (rag, hall) = missing_link(db_session)
    fake_client(monkeypatch, proposal())

    found = hypothesis.generate_hypothesis(db_session, gap)

    assert found is not None
    assert found.gap is gap
    assert "unsupported-statement rate" in found.statement
    assert found.manipulation and found.measurement
    assert found.predicted_effect and found.falsifier
    # Grounding comes off the rows the gap was built from, never off the model's prose.
    assert [(p.id, p.title) for p in found.papers] == [
        (rag.id, "Retrieval Augmented Generation"),
        (hall.id, "Measuring Hallucination"),
    ]


def test_one_claude_call_per_gap(db_session, monkeypatch):
    gap, _ = missing_link(db_session)
    messages = fake_client(monkeypatch, proposal())

    hypothesis.generate_hypothesis(db_session, gap)

    assert len(messages.calls) == 1


def test_a_contradiction_gap_is_handled_too(db_session, monkeypatch):
    gap, (pro, con) = contradiction(db_session)
    messages = fake_client(
        monkeypatch,
        proposal(
            statement=(
                "Distillation raises top-1 accuracy on in-distribution test sets by two points "
                "and lowers it by five or more once the input distribution shifts."
            ),
            falsifier=(
                "Distilled students track their teachers within one point under every "
                "distribution shift measured."
            ),
        ),
    )

    found = hypothesis.generate_hypothesis(db_session, gap)

    assert found is not None
    assert "top-1 accuracy" in found.statement
    assert [p.id for p in found.papers] == [pro.id, con.id]
    # The disagreeing sentences are what makes this gap a gap; the model must see them.
    prompt = messages.calls[0]["messages"][0]["content"]
    assert IMPROVES in prompt and DEGRADES in prompt


# --- grounded in the rows, not in the model's imagination ----------------------


def test_the_prompt_carries_the_real_abstracts_and_the_concept_neighbourhood(
    db_session, monkeypatch
):
    """'Grounded' is only true if the evidence actually went out on the wire."""
    gap, _ = missing_link(db_session)
    messages = fake_client(monkeypatch, proposal())

    hypothesis.generate_hypothesis(db_session, gap)

    prompt = messages.calls[0]["messages"][0]["content"]
    assert RAG_ABSTRACT in prompt
    assert HALLUCINATION_ABSTRACT in prompt
    # Both endpoints' neighbourhoods, including a claim about neither gap concept directly.
    assert RA_LM in prompt
    assert LM_HALLUCINATES in prompt
    assert RA_NEEDS_INDEX in prompt
    assert "Vector Index" in prompt
    assert gap.rationale in prompt


def test_a_paper_the_model_invents_never_becomes_grounding(db_session, monkeypatch):
    gap, (rag, hall) = missing_link(db_session)
    fake_client(monkeypatch, proposal(paper_ids=[9999], papers=["A Paper That Does Not Exist"]))

    found = hypothesis.generate_hypothesis(db_session, gap)

    assert found is not None
    assert all(p.id != 9999 for p in found.papers)
    assert "A Paper That Does Not Exist" not in {p.title for p in found.papers}
    assert {p.id for p in found.papers} <= {rag.id, hall.id}


def test_the_model_may_narrow_the_grounding_to_the_papers_it_used(db_session, monkeypatch):
    gap, (rag, hall) = missing_link(db_session)
    fake_client(monkeypatch, proposal(paper_ids=[hall.id]))

    found = hypothesis.generate_hypothesis(db_session, gap)

    assert [p.id for p in found.papers] == [hall.id]


# --- validate before constructing ---------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        proposal(falsifier=""),
        proposal(falsifier="   "),
        proposal(drop=("falsifier",)),
        proposal(falsifier=None),
    ],
    ids=["empty", "blank", "missing", "null"],
)
def test_a_hypothesis_without_a_falsifier_is_refused(db_session, monkeypatch, caplog, reply):
    """No falsifier, no hypothesis — it is the field a vague restatement cannot fill."""
    gap, _ = missing_link(db_session)
    fake_client(monkeypatch, reply)

    assert hypothesis.generate_hypothesis(db_session, gap) is None
    assert "hypothesis: no hypothesis" in caplog.text
    assert "falsifier" in caplog.text


@pytest.mark.parametrize(
    "field", ["statement", "manipulation", "measurement", "predicted_effect"]
)
def test_any_other_missing_field_is_refused_too(db_session, monkeypatch, caplog, field):
    gap, _ = missing_link(db_session)
    fake_client(monkeypatch, proposal(drop=(field,)))

    assert hypothesis.generate_hypothesis(db_session, gap) is None
    assert field in caplog.text


def test_a_statement_that_merely_restates_the_gap_is_refused(db_session, monkeypatch, caplog):
    gap, _ = missing_link(db_session)
    fake_client(monkeypatch, proposal(statement=RESTATEMENTS[0]))

    assert hypothesis.generate_hypothesis(db_session, gap) is None
    assert "restates" in caplog.text


def test_unparseable_output_is_logged_and_yields_nothing(db_session, monkeypatch, caplog):
    gap, _ = missing_link(db_session)
    fake_client(monkeypatch, "Sure! Here is a hypothesis for you.")

    assert hypothesis.generate_hypothesis(db_session, gap) is None
    assert "hypothesis: no hypothesis" in caplog.text


def test_an_api_error_is_logged_and_yields_nothing(db_session, monkeypatch, caplog):
    gap, _ = missing_link(db_session)
    fake_client(
        monkeypatch,
        anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")),
    )

    assert hypothesis.generate_hypothesis(db_session, gap) is None
    assert "hypothesis: no hypothesis" in caplog.text


def test_missing_api_key_still_fails_loud(db_session, monkeypatch):
    """Rules.md reserves hard failure for missing keys — no silently absent hypothesis."""
    gap, _ = missing_link(db_session)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")

    with pytest.raises(MissingAPIKeyError):
        hypothesis.generate_hypothesis(db_session, gap)
