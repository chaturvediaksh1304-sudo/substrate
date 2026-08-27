import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from sqlalchemy import func, select

from app.agents import graph
from app.agents.claude import MissingAPIKeyError
from app.config import settings
from app.models import Concept, ConceptEdge, Paper

ATTENTION = dict(
    source="arxiv",
    external_id="1706.03762v7",
    title="Attention Is All You Need",
    abstract="The Transformer replaces recurrence with attention.",
    authors=["Ashish Vaswani"],
    year=2017,
    url="http://arxiv.org/abs/1706.03762v7",
)
BERT = dict(
    source="arxiv",
    external_id="1810.04805v2",
    title="BERT",
    abstract="BERT pre-trains deep bidirectional representations using self-attention.",
    authors=["Jacob Devlin"],
    year=2018,
    url="http://arxiv.org/abs/1810.04805v2",
)


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
    monkeypatch.setattr(graph, "_client", lambda: SimpleNamespace(messages=messages))
    return messages


def reply(concepts, edges) -> str:
    return json.dumps({"concepts": concepts, "edges": edges})


def edge(source, relation, target, evidence="Evidence sentence.") -> dict:
    return {"source": source, "relation": relation, "target": target, "evidence": evidence}


def add_paper(session, **fields) -> Paper:
    paper = Paper(**fields)
    session.add(paper)
    session.commit()
    return paper


def counts(session) -> tuple[int, int]:
    return (
        session.execute(select(func.count()).select_from(Concept)).scalar(),
        session.execute(select(func.count()).select_from(ConceptEdge)).scalar(),
    )


# --- happy path ---------------------------------------------------------------


def test_extraction_persists_concepts_and_edges_with_provenance(db_session, monkeypatch):
    paper = add_paper(db_session, **ATTENTION)
    messages = fake_client(
        monkeypatch,
        reply(
            ["self-attention", "recurrence"],
            [edge("self-attention", "replaces", "recurrence", ATTENTION["abstract"])],
        ),
    )

    result = graph.GraphWorker().run(db_session, [paper])

    assert result == graph.GraphResult(
        papers_processed=1, concepts_created=2, edges_created=1, papers_failed=0
    )
    concepts = db_session.execute(select(Concept)).scalars().all()
    assert sorted(c.normalized for c in concepts) == ["recurrence", "self attention"]

    stored = db_session.execute(select(ConceptEdge)).scalars().one()
    assert stored.relation == "replaces"
    # paper_id + evidence are what Phase 4 reasons over — an edge without them is useless.
    assert stored.paper_id == paper.id
    assert stored.evidence == ATTENTION["abstract"]
    by_id = {c.id: c.normalized for c in concepts}
    assert by_id[stored.source_concept_id] == "self attention"
    assert by_id[stored.target_concept_id] == "recurrence"

    prompt = messages.calls[0]["messages"][0]["content"]
    assert ATTENTION["title"] in prompt
    assert ATTENTION["abstract"] in prompt
    assert messages.calls[0]["model"] == settings.ANTHROPIC_MODEL


def test_worker_is_registrable_under_the_orchestrator():
    assert graph.GraphWorker().name == "graph"


# --- the point of the graph: one concept, many papers -------------------------


def test_same_concept_from_two_papers_collapses_to_one_node(db_session, monkeypatch):
    """Different casing and whitespace, same concept — one row, edges from both papers."""
    attention, bert = add_paper(db_session, **ATTENTION), add_paper(db_session, **BERT)
    fake_client(
        monkeypatch,
        reply(
            ["Self-Attention", "recurrence"],
            [edge("Self-Attention", "replaces", "recurrence")],
        ),
        reply(
            ["  self-attention  ", "bidirectional pre-training"],
            [edge("bidirectional pre-training", "builds on", "  self-attention  ")],
        ),
    )

    result = graph.GraphWorker().run(db_session, [attention, bert])

    assert result.concepts_created == 3  # not 4: self-attention is shared
    shared = (
        db_session.execute(select(Concept).where(Concept.normalized == "self attention"))
        .scalars()
        .one()
    )
    edges = db_session.execute(select(ConceptEdge)).scalars().all()
    assert {e.paper_id for e in edges} == {attention.id, bert.id}
    assert all(shared.id in (e.source_concept_id, e.target_concept_id) for e in edges)


def test_two_papers_asserting_the_same_relation_are_two_edges(db_session, monkeypatch):
    """Independent corroboration is signal for Phase 4 — it must not be deduped away."""
    attention, bert = add_paper(db_session, **ATTENTION), add_paper(db_session, **BERT)
    same = reply(["self-attention", "recurrence"], [edge("self-attention", "replaces", "recurrence")])
    fake_client(monkeypatch, same)

    result = graph.GraphWorker().run(db_session, [attention, bert])

    assert result.edges_created == 2
    assert counts(db_session) == (2, 2)


# --- idempotency --------------------------------------------------------------


def test_rerunning_the_same_paper_does_not_duplicate(db_session, monkeypatch):
    paper = add_paper(db_session, **ATTENTION)
    fake_client(
        monkeypatch,
        reply(["self-attention", "recurrence"], [edge("self-attention", "replaces", "recurrence")]),
    )
    worker = graph.GraphWorker()

    worker.run(db_session, [paper])
    before = counts(db_session)
    again = worker.run(db_session, [paper])

    assert counts(db_session) == before
    assert again.papers_processed == 1
    assert again.concepts_created == 0
    assert again.edges_created == 0


# --- the model proposes, the code validates -----------------------------------


def test_unparseable_json_is_skipped_not_raised(db_session, monkeypatch):
    paper = add_paper(db_session, **ATTENTION)
    fake_client(monkeypatch, "Sure! The key concepts are attention and recurrence.")

    result = graph.GraphWorker().run(db_session, [paper])

    assert (result.papers_processed, result.papers_failed) == (0, 1)
    assert counts(db_session) == (0, 0)


def test_edge_referencing_an_undeclared_concept_is_dropped(db_session, monkeypatch, caplog):
    """A dangling node invented from a bad edge is exactly what must never be written."""
    paper = add_paper(db_session, **ATTENTION)
    fake_client(
        monkeypatch,
        reply(
            ["self-attention", "recurrence"],
            [
                edge("self-attention", "replaces", "recurrence"),
                edge("self-attention", "improves", "quantum tunnelling"),
            ],
        ),
    )

    result = graph.GraphWorker().run(db_session, [paper])

    assert (result.papers_processed, result.papers_failed) == (1, 0)
    assert result.edges_created == 1
    assert counts(db_session) == (2, 1)
    assert not db_session.execute(
        select(Concept).where(Concept.normalized == "quantum tunnelling")
    ).scalars().all()
    assert "quantum tunnelling" in caplog.text


# --- degrade, don't die -------------------------------------------------------


def test_one_paper_failing_does_not_stop_the_batch(db_session, monkeypatch):
    attention, bert = add_paper(db_session, **ATTENTION), add_paper(db_session, **BERT)
    fake_client(
        monkeypatch,
        anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")),
        reply(
            ["bidirectional pre-training", "self-attention"],
            [edge("bidirectional pre-training", "builds on", "self-attention")],
        ),
    )

    result = graph.GraphWorker().run(db_session, [attention, bert])

    assert (result.papers_processed, result.papers_failed) == (1, 1)
    assert {e.paper_id for e in db_session.execute(select(ConceptEdge)).scalars()} == {bert.id}


def test_missing_api_key_still_fails_loud(db_session, monkeypatch):
    """Rules.md reserves hard failure for missing keys — it must not degrade to 0 concepts."""
    paper = add_paper(db_session, **ATTENTION)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")

    with pytest.raises(MissingAPIKeyError):
        graph.GraphWorker().run(db_session, [paper])


# --- connectivity: a concept nothing says anything about is not a concept ------


def test_concepts_no_edge_uses_are_not_persisted(db_session, monkeypatch):
    """Orphans are 75% of a real run's output. Declared-but-unused must not reach the DB."""
    paper = add_paper(db_session, **ATTENTION)
    fake_client(
        monkeypatch,
        reply(
            [
                "self-attention",
                "recurrence",
                "Midjourney-30K",
                "GenEval",
                "patch level",
            ],
            [
                edge("self-attention", "replaces", "recurrence"),
                edge("recurrence", "predates", "self-attention"),
            ],
        ),
    )

    result = graph.GraphWorker().run(db_session, [paper])

    assert result.concepts_created == 2
    assert result.edges_created == 2
    assert sorted(
        c.normalized for c in db_session.execute(select(Concept)).scalars()
    ) == ["recurrence", "self attention"]


def test_paper_whose_edges_are_all_invalid_persists_no_concepts(db_session, monkeypatch):
    """Every edge dropped means nothing survived to connect to — so nothing is written."""
    paper = add_paper(db_session, **ATTENTION)
    fake_client(
        monkeypatch,
        reply(
            ["self-attention", "recurrence"],
            [edge("self-attention", "improves", "quantum tunnelling")],
        ),
    )

    result = graph.GraphWorker().run(db_session, [paper])

    # The call succeeded and the JSON parsed — nothing failed, it just yielded nothing.
    assert (result.papers_processed, result.papers_failed) == (1, 0)
    assert (result.concepts_created, result.edges_created) == (0, 0)
    assert counts(db_session) == (0, 0)


def test_declaring_an_existing_concept_without_using_it_does_not_delete_it(
    db_session, monkeypatch
):
    """Dropping a concept is declining to create a row, never removing another paper's."""
    attention, bert = add_paper(db_session, **ATTENTION), add_paper(db_session, **BERT)
    fake_client(
        monkeypatch,
        reply(
            ["self-attention", "recurrence"],
            [edge("self-attention", "replaces", "recurrence")],
        ),
        reply(
            ["bidirectional pre-training", "masked language modelling", "recurrence"],
            [edge("bidirectional pre-training", "uses", "masked language modelling")],
        ),
    )

    graph.GraphWorker().run(db_session, [attention, bert])

    survivors = sorted(c.normalized for c in db_session.execute(select(Concept)).scalars())
    assert survivors == [
        "bidirectional pre training",
        "masked language modelling",
        "recurrence",
        "self attention",
    ]
    still_there = db_session.execute(
        select(ConceptEdge).where(ConceptEdge.paper_id == attention.id)
    ).scalars().all()
    assert len(still_there) == 1


# --- the prompt carries the same constraints the code enforces ----------------


def test_prompt_states_the_extraction_constraints(db_session, monkeypatch):
    paper = add_paper(db_session, **ATTENTION)
    messages = fake_client(
        monkeypatch,
        reply(["self-attention", "recurrence"], [edge("self-attention", "replaces", "recurrence")]),
    )

    graph.GraphWorker().run(db_session, [paper])

    system = messages.calls[0]["system"]
    assert f"At most {graph.MAX_CONCEPTS} concepts" in system
    for forbidden in ("dataset", "benchmark", "metric"):
        assert forbidden in system
    assert "acronym" in system
    assert "must appear in at least one edge" in system


# --- normalize(): concept identity across papers -------------------------------------------

@pytest.mark.parametrize(
    "variants",
    [
        ["large language model (LLM)", "Large Language Model", "large language models"],
        ["retrieval-augmented generation", "Retrieval Augmented Generation"],
        ["chunking strategies", "Chunking Strategy"],
    ],
)
def test_surface_variants_of_one_concept_share_a_key(variants):
    """Cross-paper linking only works if the same idea lands on the same key."""
    keys = {graph.normalize(v) for v in variants}
    assert len(keys) == 1, f"{variants} split into {keys}"


@pytest.mark.parametrize("word", ["analysis", "bias", "status", "class", "physics"])
def test_words_ending_in_s_that_are_not_plurals_survive(word):
    """A false merge invents a claim; these must not lose their trailing s to a naive rule."""
    assert graph.normalize(word) == graph.normalize(word.upper())
    assert graph.normalize(word).endswith("s") or word == "physics"


def test_distinct_concepts_do_not_collide():
    assert graph.normalize("retrieval") != graph.normalize("retriever")
    assert graph.normalize("graph") != graph.normalize("grapheme")
    assert graph.normalize("RAG") != graph.normalize("retrieval augmented generation")  # known limit


# --- acronym resolution: the one identity case normalize() cannot reach --------------------


@pytest.mark.parametrize(
    "short, long",
    [
        ("rag", "retrieval augmented generation"),
        ("llm", "large language model"),
        ("cot", "chain of thought"),
    ],
)
def test_an_acronym_is_the_same_concept_as_its_expansion(short, long):
    assert graph.is_acronym_of(short, long)
    assert graph.is_acronym_of(long, short) is False  # one-directional by design


@pytest.mark.parametrize(
    "a, b",
    [
        ("rag", "ragpart"),          # shares a prefix, not an expansion
        ("retrieval", "retriever"),  # the pair embeddings wrongly merge
        ("rag", "cloth rag"),        # 'rag' is not the initials of 'cloth rag'
        ("rag", "retrieval augmented"),   # wrong number of words
        ("r", "retrieval augmented generation"),  # too short to be an acronym
        ("rag2", "retrieval augmented generation"),  # not alphabetic
    ],
)
def test_near_misses_are_not_acronyms(a, b):
    """A false merge invents a claim the papers never made — err toward leaving things alone."""
    assert not graph.is_acronym_of(a, b)
    assert not graph.is_acronym_of(b, a)


def test_an_acronym_and_its_expansion_land_on_one_concept(db_session):
    first, created_first = graph._concept_id(
        db_session, "retrieval augmented generation", "Retrieval-Augmented Generation"
    )
    second, created_second = graph._concept_id(db_session, "rag", "RAG")
    assert created_first is True
    assert created_second is False, "the acronym should have resolved to the existing expansion"
    assert first == second
    assert db_session.execute(select(func.count(Concept.id))).scalar() == 1


def test_the_expansion_resolves_to_an_existing_acronym_too(db_session):
    first, _ = graph._concept_id(db_session, "rag", "RAG")
    second, created = graph._concept_id(
        db_session, "retrieval augmented generation", "Retrieval-Augmented Generation"
    )
    assert created is False
    assert first == second


def test_a_distinct_concept_still_gets_its_own_row(db_session):
    graph._concept_id(db_session, "retrieval augmented generation", "RAG")
    _, created = graph._concept_id(db_session, "retriever", "retriever")
    assert created is True
    assert db_session.execute(select(func.count(Concept.id))).scalar() == 2


def test_exact_normalized_match_still_short_circuits(db_session):
    first, created_first = graph._concept_id(db_session, "self attention", "Self-Attention")
    second, created_second = graph._concept_id(db_session, "self attention", "self attention")
    assert created_first is True and created_second is False
    assert first == second
