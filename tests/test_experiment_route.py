import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.agents import experiment
from app.agents.claude import MissingAPIKeyError
from app.agents.gaps import GapWorker
from app.agents.graph import GraphWorker
from app.agents.hypothesis import HypothesisWorker
from app.agents.orchestrator import Orchestrator
from app.agents.retrieval import RetrievalWorker

# The stub workers, the fake Anthropic clients and the graph-seeding helpers each already
# exist one file over; a fourth copy of any of them would only rot.
from tests.test_contradictions import fake_client as fake_gap_client
from tests.test_experiment import fake_client as fake_experiment_client, proposal as design_reply
from tests.test_gap_ranking import assessment
from tests.test_gaps_route import GAP, StubGapWorker
from tests.test_hypothesis import (
    fake_client as fake_hypothesis_client,
    missing_link,
    proposal as hypothesis_reply,
)
from tests.test_hypothesis_route import HYPOTHESIS, StubHypothesisWorker

client = TestClient(main.app)

DESIGN = experiment.ExperimentDesign(
    hypothesis=HYPOTHESIS,
    method="Route 500 Konigsberg instances with and without the A-C bridge.",
    manipulated="Whether the planner is given the A-C bridge.",
    measured="Error rate on held-out routing instances.",
    controlled=["The same planner and the same instance order in both conditions."],
    expected_outcome="Error rate halves when the bridge is available.",
    discriminating_outcome="Error rate is unchanged with and without the bridge.",
    papers=HYPOTHESIS.papers,
)


class StubExperimentWorker:
    """Stands in for ExperimentWorker and records the hypotheses the orchestrator handed it."""

    name = "experiment"

    def __init__(self, produces=DESIGN, raises=None):
        self.produces = produces
        self.raises = raises
        self.calls = []

    def run(self, session, hypothesis, **kwargs):
        self.calls.append(hypothesis)
        if self.raises is not None:
            raise self.raises
        return self.produces


def install(monkeypatch, *workers):
    monkeypatch.setattr(main, "orchestrator", Orchestrator(workers=workers))
    return workers


def use_test_db(monkeypatch, db_session):
    """Point the route's session factory at the database the fixture seeded."""
    monkeypatch.setattr(main, "SessionLocal", lambda: db_session)


def mock_the_whole_chain(monkeypatch):
    """All three Claude calls — assess the gap, propose the hypothesis, design the experiment."""
    fake_gap_client(monkeypatch, assessment())
    fake_hypothesis_client(monkeypatch, hypothesis_reply())
    fake_experiment_client(monkeypatch, design_reply())


# --- the phase's goal statement: a worker agent under the orchestrator ---------


def test_orchestrator_delegates_to_the_worker_registered_as_experiment():
    """Inlining design_experiment into the orchestrator would fail here, which is the point."""
    gap_worker, hypothesis_worker, worker = (
        StubGapWorker(),
        StubHypothesisWorker(),
        StubExperimentWorker(),
    )

    found = Orchestrator(workers=[gap_worker, hypothesis_worker, worker]).design_experiments(
        None, limit=2
    )

    # Gaps out of the gaps worker, each into the hypothesis worker, each of those into this one.
    assert gap_worker.calls == [2]
    assert hypothesis_worker.calls == [GAP]
    assert worker.calls == [HYPOTHESIS]
    assert found.gaps_considered == 1
    assert found.hypotheses_considered == 1
    assert found.designs == [DESIGN]


def test_the_real_experiment_worker_is_registered_by_default_beside_the_others():
    workers = Orchestrator().workers

    assert isinstance(workers["experiment"], experiment.ExperimentWorker)
    # Registering a fifth worker must not displace the first four.
    assert isinstance(workers["retrieval"], RetrievalWorker)
    assert isinstance(workers["graph"], GraphWorker)
    assert isinstance(workers["gaps"], GapWorker)
    assert isinstance(workers["hypothesis"], HypothesisWorker)


# --- the route ----------------------------------------------------------------


def test_experiments_route_returns_a_design_per_hypothesis(db_session, monkeypatch):
    """A seeded graph plus mocked Claude: 200, and every field a reader needs."""
    missing_link(db_session)
    use_test_db(monkeypatch, db_session)
    mock_the_whole_chain(monkeypatch)

    response = client.post("/experiments", json={"limit": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["gaps_considered"] == 1
    assert body["hypotheses_considered"] == 1
    assert len(body["designs"]) == 1
    design = body["designs"][0]
    assert "long-tail entity questions" in design["method"]
    # All three variables: what is varied, what is read off, what is pinned down.
    assert "retriever precision" in design["manipulated"]
    assert "Unsupported statements per answer" in design["measured"]
    assert design["controlled"] and all(design["controlled"])
    # Both outcomes, so a reader can see the design could come out either way.
    assert "fall by roughly a third" in design["expected_outcome"]
    assert "stay flat" in design["discriminating_outcome"]
    assert design["papers"] and all(paper["title"] for paper in design["papers"])
    assert design["hypothesis"]["gap"]["kind"] == "missing_link"


def test_no_gaps_is_a_200_with_nothing_considered(monkeypatch):
    hypothesis_worker, worker = StubHypothesisWorker(), StubExperimentWorker()
    install(monkeypatch, StubGapWorker(found=()), hypothesis_worker, worker)

    response = client.post("/experiments", json={})

    assert response.status_code == 200
    assert response.json() == {
        "gaps_considered": 0,
        "hypotheses_considered": 0,
        "designs": [],
    }
    assert hypothesis_worker.calls == worker.calls == []  # nothing to work from, no API calls


def test_a_gap_that_yields_no_hypothesis_is_a_200_that_says_so(monkeypatch):
    worker = StubExperimentWorker()
    install(monkeypatch, StubGapWorker(), StubHypothesisWorker(produces=None), worker)

    response = client.post("/experiments", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["designs"] == []
    # A gap was found, no hypothesis survived: distinguishable from an empty graph.
    assert (body["gaps_considered"], body["hypotheses_considered"]) == (1, 0)
    assert worker.calls == []


def test_a_rejected_design_is_a_200_that_says_so(monkeypatch):
    """None back from the worker — the testability guard refused — is not a 500."""
    install(
        monkeypatch, StubGapWorker(), StubHypothesisWorker(), StubExperimentWorker(produces=None)
    )

    response = client.post("/experiments", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["designs"] == []
    # A hypothesis existed and no design survived: the third distinct outcome.
    assert (body["gaps_considered"], body["hypotheses_considered"]) == (1, 1)


# --- 503s: an unavailable upstream must never look like "no design" -----------


@pytest.mark.parametrize("failing", ["gaps", "hypothesis", "experiment"])
def test_experiments_route_missing_api_key_returns_503(monkeypatch, failing):
    error = MissingAPIKeyError("ANTHROPIC_API_KEY is not set")
    install(
        monkeypatch,
        StubGapWorker(raises=error if failing == "gaps" else None),
        StubHypothesisWorker(raises=error if failing == "hypothesis" else None),
        StubExperimentWorker(raises=error if failing == "experiment" else None),
    )

    response = client.post("/experiments", json={})

    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_experiments_route_upstream_anthropic_failure_returns_503(monkeypatch):
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    install(
        monkeypatch, StubGapWorker(), StubHypothesisWorker(), StubExperimentWorker(raises=error)
    )

    response = client.post("/experiments", json={})

    assert response.status_code == 503
    assert "claude" in response.json()["detail"].lower()


# --- request validation: `limit` is a spend cap, so its bounds are enforced ----


@pytest.mark.parametrize("limit", [0, experiment.MAX_EXPERIMENTS + 1])
def test_experiments_route_limit_out_of_bounds_is_rejected(limit):
    assert client.post("/experiments", json={"limit": limit}).status_code == 422


def test_experiments_route_passes_the_limit_through_to_the_workers(monkeypatch):
    gap_worker, *_ = install(
        monkeypatch, StubGapWorker(), StubHypothesisWorker(), StubExperimentWorker()
    )

    top = experiment.MAX_EXPERIMENTS
    assert client.post("/experiments", json={"limit": top}).status_code == 200
    # One knob: it caps the gaps assessed, the hypotheses attempted and the designs attempted.
    assert gap_worker.calls == [top]


# --- end to end: real SQL -> gaps -> hypothesis -> experiment -> route ---------


def test_experiments_route_end_to_end_carries_the_seeded_rows(db_session, monkeypatch):
    """Nothing injected at the top: the response's facts must be the database's rows."""
    _, (rag, hall) = missing_link(db_session)
    # Ids up front: the route closes the session it was handed, detaching these rows.
    rag_id, hall_id = rag.id, hall.id
    use_test_db(monkeypatch, db_session)
    mock_the_whole_chain(monkeypatch)

    body = client.post("/experiments", json={"limit": 1}).json()

    design = body["designs"][0]
    gap = design["hypothesis"]["gap"]
    assert (gap["concept_a"], gap["concept_b"]) == ("Retrieval Augmentation", "Hallucination")
    assert gap["evidence"] == ["Language Models"]
    assert [(p["id"], p["title"]) for p in design["papers"]] == [
        (rag_id, "Retrieval Augmented Generation"),
        (hall_id, "Measuring Hallucination"),
    ]
