import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.agents import experiment
from app.agents.claude import MissingAPIKeyError
from app.agents.hypothesis import Hypothesis
from app.config import settings

# The fake Anthropic client, the graph-seeding helpers and the gap fixtures already exist
# one and two files over; a third copy of any of them would only rot.
from tests.test_contradictions import FakeMessages
from tests.test_hypothesis import RAG_ABSTRACT, contradiction, missing_link

HYP_STATEMENT = (
    "Retrieval augmentation lowers the unsupported-statement rate on long-tail entity "
    "queries, but raises it whenever the retriever returns off-topic passages."
)
HYP_MANIPULATION = (
    "Whether the model answers with or without a dense retriever, crossed with how precise "
    "that retriever is."
)
HYP_MEASUREMENT = "Unsupported statements per answer, annotated against the passages shown."
HYP_PREDICTED = (
    "About a third fewer unsupported statements under a precise retriever, and more than the "
    "no-retrieval baseline under an imprecise one."
)
HYP_FALSIFIER = (
    "The unsupported-statement rate stays flat across every retriever precision level, or "
    "falls as precision falls."
)


def fake_client(monkeypatch, *replies) -> FakeMessages:
    messages = FakeMessages(replies)
    monkeypatch.setattr(experiment, "_client", lambda: SimpleNamespace(messages=messages))
    return messages


def rag_hypothesis(session) -> Hypothesis:
    """A real Hypothesis off real rows — what a Phase 5 run would have handed Phase 6."""
    gap, papers = missing_link(session)
    return (
        Hypothesis(
            gap=gap,
            statement=HYP_STATEMENT,
            manipulation=HYP_MANIPULATION,
            measurement=HYP_MEASUREMENT,
            predicted_effect=HYP_PREDICTED,
            falsifier=HYP_FALSIFIER,
            papers=gap.papers,
        ),
        papers,
    )


def proposal(drop=(), **overrides) -> str:
    payload = {
        "method": (
            "Answer 500 long-tail entity questions under four conditions: no retriever, and a "
            "dense retriever whose index has been corrupted to precision 0.9, 0.6 and 0.3. "
            "Two annotators mark every claim in each answer as supported or unsupported by "
            "the passages the model was shown."
        ),
        "manipulated": (
            "Presence of the dense retriever (on or off), crossed with retriever precision set "
            "to 0.9, 0.6 and 0.3 by corrupting the index."
        ),
        "measured": (
            "Unsupported statements per answer, counted by two annotators against the passages "
            "the model was shown."
        ),
        "controlled": [
            "The same base language model and decoding settings in every condition.",
            "The same 500 questions, in the same order, across all four conditions.",
        ],
        "expected_outcome": (
            "Unsupported statements per answer fall by roughly a third at precision 0.9 and "
            "rise above the no-retriever baseline at precision 0.3."
        ),
        "discriminating_outcome": (
            "Unsupported statements per answer stay flat across all four conditions, or fall "
            "as retriever precision falls."
        ),
        "paper_ids": [],
        **overrides,
    }
    return json.dumps({k: v for k, v in payload.items() if k not in drop})


# --- the testability guard: the machine-checkable half, no model, no key ---------------
#
# Numbers behind these pairs (shared substantive terms, manipulated / measured), measured
# with the same tokenizer the guard uses:
#   on-target                        3 / 6      on-target, reworded            3 / 5
#   distillation on-target           8 / 5      off-target (latency)           0 / 0
#   off-target (fluency)             0 / 1      plausible-but-adjacent         0 / 0
#   half-right (manipulation only)   3 / 0      adjacent prose                 1 / 0
# The threshold of 2 sits in the empty band between 1 and 3.

ON_TARGET = [
    (
        "Presence of the dense retriever (on or off), crossed with retriever precision set to "
        "0.9, 0.6 and 0.3 by corrupting the index.",
        "Unsupported statements per answer, counted by two annotators against the passages "
        "the model was shown.",
    ),
    (
        "Answers generated with a dense retriever versus a closed-book baseline, at three "
        "retriever precision levels.",
        "The rate of unsupported statements in each answer, judged against the retrieved "
        "passages.",
    ),
]

OFF_TARGET = [
    # Measures throughput, not truthfulness.
    (
        "Batch size and learning rate of the fine-tuning run.",
        "Wall-clock latency and GPU memory footprint at inference time.",
    ),
    # Plausible retrieval experiment, wrong dependent variable.
    (
        "Number of training epochs for the reranker.",
        "Human preference ratings of answer fluency.",
    ),
    # Adjacent prose: a real retrieval study that cannot produce the hypothesis's falsifier.
    (
        "Which corpus the index is built over: Wikipedia, PubMed or Common Crawl.",
        "Exact-match score on open-domain question answering.",
    ),
    # Manipulates the right thing and then measures something else — the failure mode.
    (
        "Presence of the dense retriever, crossed with retriever precision.",
        "Wall-clock latency per query.",
    ),
]


@pytest.mark.parametrize("manipulated,measured", ON_TARGET)
def test_a_design_that_tests_the_hypothesis_passes_the_guard(manipulated, measured):
    assert not experiment.measures_something_else(
        manipulated, measured, HYP_MANIPULATION, HYP_MEASUREMENT
    )


@pytest.mark.parametrize("manipulated,measured", OFF_TARGET)
def test_a_design_that_measures_something_else_is_caught(manipulated, measured):
    assert experiment.measures_something_else(
        manipulated, measured, HYP_MANIPULATION, HYP_MEASUREMENT
    )


def test_empty_variables_are_off_target():
    assert experiment.measures_something_else("", "", HYP_MANIPULATION, HYP_MEASUREMENT)


def test_shared_terms_reports_the_margin():
    """Exposed so a caller can see how close a design sat to the threshold."""
    shared = experiment.shared_terms(ON_TARGET[0][1], HYP_MEASUREMENT)
    assert {"unsupported", "statements", "passages"} <= shared
    assert experiment.shared_terms(OFF_TARGET[0][1], HYP_MEASUREMENT) == set()


# --- the happy path ------------------------------------------------------------


def test_a_hypothesis_becomes_a_design_with_every_field_filled(db_session, monkeypatch):
    hypothesis, (rag, hall) = rag_hypothesis(db_session)
    fake_client(monkeypatch, proposal())

    found = experiment.design_experiment(db_session, hypothesis)

    assert found is not None
    assert found.hypothesis is hypothesis
    assert "500 long-tail entity questions" in found.method
    assert found.manipulated and found.measured
    assert len(found.controlled) == 2
    assert found.expected_outcome and found.discriminating_outcome
    # Grounding comes off the rows the hypothesis carried, never off the model's prose.
    assert [(p.id, p.title) for p in found.papers] == [
        (rag.id, "Retrieval Augmented Generation"),
        (hall.id, "Measuring Hallucination"),
    ]


def test_one_claude_call_per_hypothesis(db_session, monkeypatch):
    hypothesis, _ = rag_hypothesis(db_session)
    messages = fake_client(monkeypatch, proposal())

    experiment.design_experiment(db_session, hypothesis)

    assert len(messages.calls) == 1


def test_the_prompt_carries_the_hypothesis_it_has_to_test(db_session, monkeypatch):
    """'Tests this hypothesis' is only true if the hypothesis actually went out on the wire."""
    hypothesis, _ = rag_hypothesis(db_session)
    messages = fake_client(monkeypatch, proposal())

    experiment.design_experiment(db_session, hypothesis)

    prompt = messages.calls[0]["messages"][0]["content"]
    assert HYP_STATEMENT in prompt
    assert HYP_MANIPULATION in prompt
    assert HYP_MEASUREMENT in prompt
    assert HYP_FALSIFIER in prompt
    assert HYP_PREDICTED in prompt
    # And the rows behind it, so the method can lean on what the papers actually did.
    assert RAG_ABSTRACT in prompt
    assert "Retrieval Augmented Generation" in prompt


def test_a_paper_the_model_invents_never_becomes_grounding(db_session, monkeypatch):
    hypothesis, (rag, hall) = rag_hypothesis(db_session)
    fake_client(monkeypatch, proposal(paper_ids=[9999], papers=["A Paper That Does Not Exist"]))

    found = experiment.design_experiment(db_session, hypothesis)

    assert found is not None
    assert all(p.id != 9999 for p in found.papers)
    assert "A Paper That Does Not Exist" not in {p.title for p in found.papers}
    assert {p.id for p in found.papers} <= {rag.id, hall.id}


def test_the_model_may_narrow_the_grounding_to_the_papers_it_used(db_session, monkeypatch):
    hypothesis, (rag, hall) = rag_hypothesis(db_session)
    fake_client(monkeypatch, proposal(paper_ids=[hall.id]))

    found = experiment.design_experiment(db_session, hypothesis)

    assert [p.id for p in found.papers] == [hall.id]


def test_a_contradiction_hypothesis_is_handled_too(db_session, monkeypatch):
    gap, (pro, con) = contradiction(db_session)
    hypothesis = Hypothesis(
        gap=gap,
        statement=(
            "Distillation raises top-1 accuracy on in-distribution test sets by two points "
            "and lowers it by five or more once the input distribution shifts."
        ),
        manipulation=(
            "Whether the student is distilled from the teacher or trained from scratch, and "
            "how far the evaluation distribution shifts."
        ),
        measurement="Top-1 accuracy on in-distribution and shifted test sets.",
        predicted_effect="Two points up in distribution, five or more down under shift.",
        falsifier=(
            "Distilled students track their teachers within one point under every "
            "distribution shift measured."
        ),
        papers=gap.papers,
    )
    fake_client(
        monkeypatch,
        proposal(
            manipulated=(
                "Whether the student is distilled from the teacher or trained from scratch, "
                "at four levels of evaluation distribution shift."
            ),
            measured=(
                "Top-1 accuracy on the in-distribution test set and on each shifted test set."
            ),
        ),
    )

    found = experiment.design_experiment(db_session, hypothesis)

    assert found is not None
    assert "top-1 accuracy" in found.measured.lower()
    assert [p.id for p in found.papers] == [pro.id, con.id]


# --- validate before constructing ---------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        proposal(discriminating_outcome=""),
        proposal(discriminating_outcome="   "),
        proposal(drop=("discriminating_outcome",)),
        proposal(discriminating_outcome=None),
    ],
    ids=["empty", "blank", "missing", "null"],
)
def test_a_design_without_a_discriminating_outcome_is_refused(
    db_session, monkeypatch, caplog, reply
):
    """No result that would come out the other way, no test — only a demonstration."""
    hypothesis, _ = rag_hypothesis(db_session)
    fake_client(monkeypatch, reply)

    assert experiment.design_experiment(db_session, hypothesis) is None
    assert "experiment: no design" in caplog.text
    assert "discriminating_outcome" in caplog.text


@pytest.mark.parametrize("field", ["method", "manipulated", "measured", "expected_outcome"])
def test_any_other_missing_field_is_refused_too(db_session, monkeypatch, caplog, field):
    hypothesis, _ = rag_hypothesis(db_session)
    fake_client(monkeypatch, proposal(drop=(field,)))

    assert experiment.design_experiment(db_session, hypothesis) is None
    assert field in caplog.text


@pytest.mark.parametrize("controlled", [[], ["", "  "], None], ids=["empty", "blank", "null"])
def test_a_design_holding_nothing_constant_is_refused(
    db_session, monkeypatch, caplog, controlled
):
    hypothesis, _ = rag_hypothesis(db_session)
    fake_client(monkeypatch, proposal(controlled=controlled))

    assert experiment.design_experiment(db_session, hypothesis) is None
    assert "controlled" in caplog.text


def test_a_design_that_measures_something_else_is_refused(db_session, monkeypatch, caplog):
    hypothesis, _ = rag_hypothesis(db_session)
    manipulated, measured = OFF_TARGET[3]
    fake_client(monkeypatch, proposal(manipulated=manipulated, measured=measured))

    assert experiment.design_experiment(db_session, hypothesis) is None
    assert "does not test" in caplog.text


def test_unparseable_output_is_logged_and_yields_nothing(db_session, monkeypatch, caplog):
    hypothesis, _ = rag_hypothesis(db_session)
    fake_client(monkeypatch, "Sure! Here is an experiment design for you.")

    assert experiment.design_experiment(db_session, hypothesis) is None
    assert "experiment: no design" in caplog.text


def test_an_api_error_is_logged_and_yields_nothing(db_session, monkeypatch, caplog):
    hypothesis, _ = rag_hypothesis(db_session)
    fake_client(
        monkeypatch,
        anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")),
    )

    assert experiment.design_experiment(db_session, hypothesis) is None
    assert "experiment: no design" in caplog.text


def test_missing_api_key_still_fails_loud(db_session, monkeypatch):
    """Rules.md reserves hard failure for missing keys — no silently absent design."""
    hypothesis, _ = rag_hypothesis(db_session)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")

    with pytest.raises(MissingAPIKeyError):
        experiment.design_experiment(db_session, hypothesis)
