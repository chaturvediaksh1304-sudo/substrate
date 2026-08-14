import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import main
from app.agents import graph
from app.agents.claude import MissingAPIKeyError
from app.agents.orchestrator import Orchestrator
from app.models import Concept, ConceptEdge, Paper

client = TestClient(main.app)


# --- seeding ------------------------------------------------------------------


def add_paper(session, external_id, title) -> Paper:
    paper = Paper(
        source="arxiv",
        external_id=external_id,
        title=title,
        abstract="Abstract of " + title,
        authors=["A. Author"],
        year=2020,
        url=f"http://arxiv.org/abs/{external_id}",
    )
    session.add(paper)
    session.commit()
    return paper


def add_concept(session, name) -> Concept:
    concept = Concept(name=name, normalized=graph.normalize(name))
    session.add(concept)
    session.commit()
    return concept


def link(session, source, relation, target, paper, evidence="Because the abstract says so.") -> None:
    session.add(
        ConceptEdge(
            source_concept_id=source.id,
            target_concept_id=target.id,
            relation=relation,
            paper_id=paper.id,
            evidence=evidence,
        )
    )
    session.commit()


def chain(session):
    """A -(p1)-> B -(p2)-> C -(p2)-> D, i.e. D is three hops from A."""
    p1, p2 = add_paper(session, "1", "Paper One"), add_paper(session, "2", "Paper Two")
    a, b, c, d = (add_concept(session, n) for n in ("A", "B", "C", "D"))
    link(session, a, "improves", b, p1, "A improves B.")
    link(session, b, "builds on", c, p2, "B builds on C.")
    link(session, c, "contradicts", d, p2, "C contradicts D.")
    return {"papers": (p1, p2), "concepts": (a, b, c, d)}


def names(subgraph) -> set[str]:
    return {c.normalized for c in subgraph.concepts}


# --- one hop ------------------------------------------------------------------


def test_one_hop_returns_direct_neighbours_with_provenance(db_session):
    seeded = chain(db_session)
    p1, _ = seeded["papers"]

    subgraph = graph.traverse(db_session, "A", depth=1)

    assert subgraph.found is True
    assert names(subgraph) == {"a", "b"}
    assert [(c.normalized, c.hops) for c in subgraph.concepts] == [("a", 0), ("b", 1)]

    edge = next(e for e in subgraph.edges if e.target == "B")
    assert (edge.source, edge.relation) == ("A", "improves")
    assert edge.evidence == "A improves B."
    assert edge.paper_id == p1.id
    assert edge.title == "Paper One"
    assert (edge.year, edge.url) == (2020, "http://arxiv.org/abs/1")


def test_the_starting_concept_matches_on_normalized_form(db_session):
    chain(db_session)

    assert graph.traverse(db_session, "  a  ", depth=1).found is True


def test_a_concept_with_no_edges_is_found_but_alone(db_session):
    add_concept(db_session, "orphan")

    subgraph = graph.traverse(db_session, "orphan")

    assert subgraph.found is True
    assert names(subgraph) == {"orphan"}
    assert subgraph.edges == []


# --- the criterion: relationships across multiple papers ----------------------


def test_multi_hop_spans_two_papers(db_session):
    """A -(paper 1)-> B -(paper 2)-> C: depth 2 from A must reach C via both papers."""
    seeded = chain(db_session)
    p1, p2 = seeded["papers"]

    subgraph = graph.traverse(db_session, "A", depth=2)

    assert names(subgraph) == {"a", "b", "c"}
    hops = {c.normalized: c.hops for c in subgraph.concepts}
    assert (hops["b"], hops["c"]) == (1, 2)

    path = {(e.source, e.relation, e.target): e.paper_id for e in subgraph.edges}
    assert path[("A", "improves", "B")] == p1.id
    assert path[("B", "builds on", "C")] == p2.id
    # The whole point: the two legs of the path were asserted by different papers.
    assert len({e.paper_id for e in subgraph.edges}) == 2


# --- both directions ----------------------------------------------------------


def test_a_concept_reachable_only_inbound_is_found(db_session):
    """`A improves B` is a relationship of B just as much as of A."""
    paper = add_paper(db_session, "1", "Paper One")
    a, b = add_concept(db_session, "A"), add_concept(db_session, "B")
    link(db_session, a, "improves", b, paper)

    subgraph = graph.traverse(db_session, "B", depth=1)

    assert names(subgraph) == {"a", "b"}
    assert [(e.source, e.target) for e in subgraph.edges] == [("A", "B")]


# --- cycles -------------------------------------------------------------------


def test_a_cycle_terminates(db_session):
    """Two papers can assert A->B and B->A. An unguarded CTE would never return."""
    p1, p2 = add_paper(db_session, "1", "Paper One"), add_paper(db_session, "2", "Paper Two")
    a, b = add_concept(db_session, "A"), add_concept(db_session, "B")
    link(db_session, a, "builds on", b, p1)
    link(db_session, b, "builds on", a, p2)
    # Postgres, not pytest, is the stopwatch: a regression aborts loudly instead of hanging CI.
    db_session.execute(text("SET LOCAL statement_timeout = 5000"))

    subgraph = graph.traverse(db_session, "A", depth=graph.MAX_DEPTH)

    assert names(subgraph) == {"a", "b"}
    assert len(subgraph.edges) == 2


def test_a_self_loop_terminates(db_session):
    paper = add_paper(db_session, "1", "Paper One")
    a = add_concept(db_session, "A")
    link(db_session, a, "relates to", a, paper)
    db_session.execute(text("SET LOCAL statement_timeout = 5000"))

    subgraph = graph.traverse(db_session, "A", depth=graph.MAX_DEPTH)

    assert names(subgraph) == {"a"}
    assert len(subgraph.edges) == 1


# --- depth --------------------------------------------------------------------


def test_depth_is_honoured(db_session):
    chain(db_session)

    assert names(graph.traverse(db_session, "A", depth=2)) == {"a", "b", "c"}
    assert names(graph.traverse(db_session, "A", depth=3)) == {"a", "b", "c", "d"}


def test_depth_is_clamped_to_the_maximum(db_session):
    chain(db_session)

    assert graph.traverse(db_session, "A", depth=99).depth == graph.MAX_DEPTH


# --- unknown concept is a result, not an error --------------------------------


def test_unknown_concept_returns_an_empty_subgraph(db_session):
    chain(db_session)

    subgraph = graph.traverse(db_session, "quantum tunnelling")

    assert subgraph.found is False
    assert (subgraph.concepts, subgraph.edges) == ([], [])


# --- criterion 3: the orchestrator delegates to the graph worker ---------------


class StubGraphWorker:
    """Stands in for GraphWorker and records how it was called."""

    name = "graph"

    def __init__(self, subgraph=None, result=None, raises=None):
        self.subgraph = subgraph or graph.Subgraph(
            concept="A", depth=1, found=True, concepts=[], edges=[]
        )
        self.result = result or graph.GraphResult(papers_processed=2, concepts_created=5, edges_created=3)
        self.raises = raises
        self.traversals = []
        self.runs = []

    def run(self, session, papers, **kwargs):
        self.runs.append(list(papers))
        if self.raises:
            raise self.raises
        return self.result

    def traverse(self, session, concept, depth=graph.DEFAULT_DEPTH):
        self.traversals.append((concept, depth))
        return self.subgraph


def test_orchestrator_delegates_traversal_to_the_worker_registered_as_graph():
    worker = StubGraphWorker()

    subgraph = Orchestrator(workers=[worker]).relate(None, "A", depth=3)

    assert worker.traversals == [("A", 3)]
    assert subgraph is worker.subgraph


def test_the_real_graph_worker_is_registered_by_default():
    assert isinstance(Orchestrator().workers["graph"], graph.GraphWorker)


def test_graph_worker_traverse_reaches_the_database(db_session):
    """The worker's own method, not just the module function — that is what gets delegated to."""
    chain(db_session)

    assert names(graph.GraphWorker().traverse(db_session, "A", depth=1)) == {"a", "b"}


# --- routes -------------------------------------------------------------------


def install(monkeypatch, worker):
    monkeypatch.setattr(main, "orchestrator", Orchestrator(workers=[worker]))
    return worker


def test_traverse_route_returns_the_subgraph(monkeypatch):
    worker = install(
        monkeypatch,
        StubGraphWorker(
            subgraph=graph.Subgraph(
                concept="A",
                depth=2,
                found=True,
                concepts=[graph.RelatedConcept(id=1, name="A", normalized="a", hops=0)],
                edges=[
                    graph.RelatedEdge(
                        source="A",
                        relation="improves",
                        target="B",
                        evidence="A improves B.",
                        paper_id=7,
                        title="Paper One",
                        year=2020,
                        url="http://arxiv.org/abs/1",
                    )
                ],
            )
        ),
    )

    response = client.post("/graph/traverse", json={"concept": "A", "depth": 2})

    assert response.status_code == 200
    assert worker.traversals == [("A", 2)]
    body = response.json()
    assert body["found"] is True
    assert body["concepts"] == [{"id": 1, "name": "A", "normalized": "a", "hops": 0}]
    assert body["edges"][0]["paper_id"] == 7
    assert body["edges"][0]["evidence"] == "A improves B."


def test_traverse_route_unknown_concept_is_a_200_not_a_404(monkeypatch):
    install(
        monkeypatch,
        StubGraphWorker(
            subgraph=graph.Subgraph(concept="x", depth=2, found=False, concepts=[], edges=[])
        ),
    )

    response = client.post("/graph/traverse", json={"concept": "x"})

    assert response.status_code == 200
    body = response.json()
    assert body["found"] is False
    assert (body["concepts"], body["edges"]) == ([], [])


@pytest.mark.parametrize("payload", [{}, {"concept": "  "}, {"concept": "A", "depth": 0}])
def test_traverse_route_rejects_bad_requests(payload):
    assert client.post("/graph/traverse", json=payload).status_code == 422


def test_build_route_returns_the_counts(monkeypatch):
    worker = install(monkeypatch, StubGraphWorker())

    response = client.post("/graph/build", json={"limit": 3})

    assert response.status_code == 200
    assert response.json() == {
        "papers_processed": 2,
        "concepts_created": 5,
        "edges_created": 3,
        "papers_failed": 0,
    }
    assert len(worker.runs) == 1


def test_build_route_missing_api_key_returns_503(monkeypatch):
    install(monkeypatch, StubGraphWorker(raises=MissingAPIKeyError("ANTHROPIC_API_KEY is not set")))

    response = client.post("/graph/build", json={"limit": 1})

    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_build_route_upstream_anthropic_failure_returns_503(monkeypatch):
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    install(monkeypatch, StubGraphWorker(raises=error))

    response = client.post("/graph/build", json={"limit": 1})

    assert response.status_code == 503
    assert "claude" in response.json()["detail"].lower()
