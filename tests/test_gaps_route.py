import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.agents import gaps
from app.agents.claude import MissingAPIKeyError
from app.agents.graph import GraphWorker
from app.agents.orchestrator import Orchestrator
from app.agents.retrieval import RetrievalWorker

# The fake Anthropic client and the graph-seeding helpers each already exist one file
# over; a second copy of either would only rot.
from tests.test_contradictions import fake_client
from tests.test_gap_ranking import assessment, mixed_graph

client = TestClient(main.app)

GAP = gaps.CandidateGap(
    kind="missing_link",
    concept_a_id=1,
    concept_a="A",
    concept_b_id=2,
    concept_b="C",
    papers=[gaps.GapPaper(id=7, title="Bridges of Konigsberg")],
    evidence=["B"],
    prescore=2,
    significance=3,
    rationale="Nobody has joined these up.",
)


class StubGapWorker:
    """Stands in for GapWorker and records how the orchestrator called it."""

    name = "gaps"

    def __init__(self, found=(GAP,), raises=None):
        self.found = list(found)
        self.raises = raises
        self.calls = []

    def run(self, session, limit=gaps.RANK_LIMIT, **kwargs):
        self.calls.append(limit)
        if self.raises is not None:
            raise self.raises
        return self.found


def install(monkeypatch, worker):
    monkeypatch.setattr(main, "orchestrator", Orchestrator(workers=[worker]))
    return worker


def use_test_db(monkeypatch, db_session):
    """Point the route's session factory at the database the fixture seeded."""
    monkeypatch.setattr(main, "SessionLocal", lambda: db_session)


# --- the phase's goal statement: a worker agent under the orchestrator ---------


def test_orchestrator_delegates_to_the_worker_registered_as_gaps():
    """Inlining rank_gaps into the orchestrator would fail here, which is the point."""
    worker = StubGapWorker()

    found = Orchestrator(workers=[worker]).find_gaps(None, limit=3)

    assert worker.calls == [3]
    assert found == [GAP]


def test_the_real_gap_worker_is_registered_by_default_beside_the_others():
    workers = Orchestrator().workers

    assert isinstance(workers["gaps"], gaps.GapWorker)
    # Registering a third worker must not displace the first two.
    assert isinstance(workers["retrieval"], RetrievalWorker)
    assert isinstance(workers["graph"], GraphWorker)


# --- the route ----------------------------------------------------------------


def test_gaps_route_returns_a_ranked_list(db_session, monkeypatch):
    """A seeded graph plus mocked assessments: 200, and every field a reader needs."""
    mixed_graph(db_session)
    use_test_db(monkeypatch, db_session)
    fake_client(monkeypatch, assessment())

    response = client.post("/gaps", json={})

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list) and len(body) == 2
    for gap in body:
        assert gap["kind"] in ("missing_link", "contradiction")
        assert gap["concept_a"] and gap["concept_b"]
        assert gap["papers"] and all(paper["title"] for paper in gap["papers"])
        assert gap["significance"] == 2
        assert gap["rationale"] == "Nobody has joined these up."


def test_gaps_route_empty_graph_is_a_200_with_no_claude_call(db_session, monkeypatch):
    """Nothing to assess is a legitimate answer, and must not cost an API call."""
    use_test_db(monkeypatch, db_session)
    messages = fake_client(monkeypatch, assessment())

    response = client.post("/gaps", json={})

    assert response.status_code == 200
    assert response.json() == []
    assert messages.calls == []


# --- 503s: an unavailable upstream must never look like an empty gap list ------


def test_gaps_route_missing_api_key_returns_503(monkeypatch):
    install(monkeypatch, StubGapWorker(raises=MissingAPIKeyError("ANTHROPIC_API_KEY is not set")))

    response = client.post("/gaps", json={})

    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_gaps_route_upstream_anthropic_failure_returns_503(monkeypatch):
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    install(monkeypatch, StubGapWorker(raises=error))

    response = client.post("/gaps", json={})

    assert response.status_code == 503
    assert "claude" in response.json()["detail"].lower()


# --- request validation: `limit` is a spend cap, so its bounds are enforced ----


@pytest.mark.parametrize("limit", [0, 51])
def test_gaps_route_limit_out_of_bounds_is_rejected(limit):
    assert client.post("/gaps", json={"limit": limit}).status_code == 422


def test_gaps_route_passes_the_limit_through_to_the_worker(monkeypatch):
    worker = install(monkeypatch, StubGapWorker())

    assert client.post("/gaps", json={"limit": 4}).status_code == 200
    assert worker.calls == [4]


# --- end to end: real SQL -> worker -> orchestrator -> route, only Claude faked -


def test_gaps_route_end_to_end_carries_the_seeded_rows(db_session, monkeypatch):
    """Nothing injected at the top: the response's facts must be the database's rows."""
    seeded = mixed_graph(db_session)
    # Ids up front: the route closes the session it was handed, detaching these rows.
    bridge_id, pro_id, con_id = (paper.id for paper in seeded["papers"])
    use_test_db(monkeypatch, db_session)
    fake_client(monkeypatch, assessment())

    body = client.post("/gaps", json={"limit": 5}).json()

    by_kind = {gap["kind"]: gap for gap in body}
    assert (by_kind["contradiction"]["concept_a"], by_kind["contradiction"]["concept_b"]) == (
        "Distillation",
        "Accuracy",
    )
    assert [(p["id"], p["title"]) for p in by_kind["contradiction"]["papers"]] == [
        (pro_id, "Distillation Improves Accuracy"),
        (con_id, "Distillation Degrades Accuracy"),
    ]
    assert sorted(by_kind["contradiction"]["evidence"]) == sorted(
        [
            "Distillation improves accuracy on every benchmark we tried.",
            "Distillation degrades accuracy under distribution shift.",
        ]
    )
    assert (by_kind["missing_link"]["concept_a"], by_kind["missing_link"]["concept_b"]) == ("A", "C")
    assert by_kind["missing_link"]["evidence"] == ["B"]
    assert [(p["id"], p["title"]) for p in by_kind["missing_link"]["papers"]] == [
        (bridge_id, "Bridges of Konigsberg")
    ]
