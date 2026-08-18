import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.agents import hypothesis
from app.agents.claude import MissingAPIKeyError
from app.agents.gaps import GapWorker
from app.agents.graph import GraphWorker
from app.agents.orchestrator import Orchestrator
from app.agents.retrieval import RetrievalWorker

# The stub gap worker, the fake Anthropic clients and the graph-seeding helpers each
# already exist one file over; a second copy of any of them would only rot.
from tests.test_contradictions import fake_client as fake_gap_client
from tests.test_gap_ranking import assessment
from tests.test_gaps_route import GAP, StubGapWorker
from tests.test_hypothesis import fake_client as fake_hypothesis_client, missing_link, proposal

client = TestClient(main.app)

HYPOTHESIS = hypothesis.Hypothesis(
    gap=GAP,
    statement="Bridging A to C halves the error rate on Konigsberg routing instances.",
    manipulation="Whether the route planner is given the A-C bridge or not.",
    measurement="Error rate on held-out routing instances.",
    predicted_effect="About half the errors, and no change in runtime.",
    falsifier="Error rate is unchanged with and without the bridge.",
    papers=GAP.papers,
)


class StubHypothesisWorker:
    """Stands in for HypothesisWorker and records the gaps the orchestrator handed it."""

    name = "hypothesis"

    def __init__(self, produces=HYPOTHESIS, raises=None):
        self.produces = produces
        self.raises = raises
        self.calls = []

    def run(self, session, gap, **kwargs):
        self.calls.append(gap)
        if self.raises is not None:
            raise self.raises
        return self.produces


def install(monkeypatch, *workers):
    monkeypatch.setattr(main, "orchestrator", Orchestrator(workers=workers))
    return workers


def use_test_db(monkeypatch, db_session):
    """Point the route's session factory at the database the fixture seeded."""
    monkeypatch.setattr(main, "SessionLocal", lambda: db_session)


# --- the phase's goal statement: a worker agent under the orchestrator ---------


def test_orchestrator_delegates_to_the_worker_registered_as_hypothesis():
    """Inlining generate_hypothesis into the orchestrator would fail here, which is the point."""
    gap_worker, worker = StubGapWorker(), StubHypothesisWorker()

    found = Orchestrator(workers=[gap_worker, worker]).hypothesize(None, limit=2)

    # The gaps come from the gaps worker, and each one goes back out to the hypothesis worker.
    assert gap_worker.calls == [2]
    assert worker.calls == [GAP]
    assert found.gaps_considered == 1
    assert found.hypotheses == [HYPOTHESIS]


def test_the_real_hypothesis_worker_is_registered_by_default_beside_the_others():
    workers = Orchestrator().workers

    assert isinstance(workers["hypothesis"], hypothesis.HypothesisWorker)
    # Registering a fourth worker must not displace the first three.
    assert isinstance(workers["retrieval"], RetrievalWorker)
    assert isinstance(workers["graph"], GraphWorker)
    assert isinstance(workers["gaps"], GapWorker)


# --- the route ----------------------------------------------------------------


def test_hypotheses_route_returns_a_hypothesis_per_gap(db_session, monkeypatch):
    """A seeded graph plus mocked Claude: 200, and every field a reader needs."""
    missing_link(db_session)
    use_test_db(monkeypatch, db_session)
    fake_gap_client(monkeypatch, assessment())
    fake_hypothesis_client(monkeypatch, proposal())

    response = client.post("/hypotheses", json={"limit": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["gaps_considered"] == 1
    assert len(body["hypotheses"]) == 1
    found = body["hypotheses"][0]
    assert "unsupported-statement rate" in found["statement"]
    assert found["manipulation"] and found["measurement"]
    assert found["predicted_effect"] and found["falsifier"]
    assert found["papers"] and all(paper["title"] for paper in found["papers"])
    assert found["gap"]["kind"] == "missing_link"


def test_a_rejected_generation_is_a_200_that_says_so(monkeypatch):
    """None back from the worker is a legitimate outcome, not a 500 — and it must read as one."""
    install(monkeypatch, StubGapWorker(), StubHypothesisWorker(produces=None))

    response = client.post("/hypotheses", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["hypotheses"] == []
    # A gap was considered and nothing came of it — distinguishable from an empty graph below.
    assert body["gaps_considered"] == 1


def test_no_gaps_is_a_200_with_nothing_considered(monkeypatch):
    worker = StubHypothesisWorker()
    install(monkeypatch, StubGapWorker(found=()), worker)

    response = client.post("/hypotheses", json={})

    assert response.status_code == 200
    assert response.json() == {"gaps_considered": 0, "hypotheses": []}
    assert worker.calls == []  # no gap, no pointless API call


# --- 503s: an unavailable upstream must never look like "no hypothesis" -------


@pytest.mark.parametrize("failing", ["gaps", "hypothesis"])
def test_hypotheses_route_missing_api_key_returns_503(monkeypatch, failing):
    error = MissingAPIKeyError("ANTHROPIC_API_KEY is not set")
    install(
        monkeypatch,
        StubGapWorker(raises=error if failing == "gaps" else None),
        StubHypothesisWorker(raises=error if failing == "hypothesis" else None),
    )

    response = client.post("/hypotheses", json={})

    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_hypotheses_route_upstream_anthropic_failure_returns_503(monkeypatch):
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    install(monkeypatch, StubGapWorker(), StubHypothesisWorker(raises=error))

    response = client.post("/hypotheses", json={})

    assert response.status_code == 503
    assert "claude" in response.json()["detail"].lower()


# --- request validation: `limit` is a spend cap, so its bounds are enforced ----


@pytest.mark.parametrize("limit", [0, 11])
def test_hypotheses_route_limit_out_of_bounds_is_rejected(limit):
    assert client.post("/hypotheses", json={"limit": limit}).status_code == 422


def test_hypotheses_route_passes_the_limit_through_to_the_workers(monkeypatch):
    gap_worker, _ = install(monkeypatch, StubGapWorker(), StubHypothesisWorker())

    assert client.post("/hypotheses", json={"limit": 4}).status_code == 200
    # One knob: it caps the gaps assessed as well as the hypotheses attempted.
    assert gap_worker.calls == [4]


# --- end to end: real SQL -> gaps -> worker -> orchestrator -> route -----------


def test_hypotheses_route_end_to_end_carries_the_seeded_rows(db_session, monkeypatch):
    """Nothing injected at the top: the response's facts must be the database's rows."""
    _, (rag, hall) = missing_link(db_session)
    # Ids up front: the route closes the session it was handed, detaching these rows.
    rag_id, hall_id = rag.id, hall.id
    use_test_db(monkeypatch, db_session)
    fake_gap_client(monkeypatch, assessment())
    fake_hypothesis_client(monkeypatch, proposal())

    body = client.post("/hypotheses", json={"limit": 1}).json()

    found = body["hypotheses"][0]
    assert (found["gap"]["concept_a"], found["gap"]["concept_b"]) == (
        "Retrieval Augmentation",
        "Hallucination",
    )
    assert found["gap"]["evidence"] == ["Language Models"]
    assert [(p["id"], p["title"]) for p in found["papers"]] == [
        (rag_id, "Retrieval Augmented Generation"),
        (hall_id, "Measuring Hallucination"),
    ]
