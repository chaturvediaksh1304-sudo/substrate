import json
import logging
from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import Integer, func, literal, select
from sqlalchemy.dialects.postgresql import ARRAY, array, insert
from sqlalchemy.orm import Session, aliased

from app.agents.claude import MissingAPIKeyError, _client
from app.config import settings
from app.models import Concept, ConceptEdge, Paper

log = logging.getLogger(__name__)

# A title + abstract yields a handful of concepts and edges; this is room to spare.
MAX_TOKENS = 2000

# Two hops is "what does this relate to, and what do those relate to" — the question
# Phase 4 asks. ponytail: the CTE enumerates simple paths, so cost grows with branching;
# 4 is where that stays cheap on a corpus this size. Raise it only with numbers in hand.
DEFAULT_DEPTH = 2
MAX_DEPTH = 4

# An abstract states one or two claims. The first real run averaged ~15 concepts a paper
# and 75% of them were orphans, so the ceiling is set where a connected graph is still
# plausible: 8 concepts is four edges' worth of endpoints.
MAX_CONCEPTS = 8

SYSTEM = (
    "You extract a knowledge graph from a paper's title and abstract.\n"
    "Reply with JSON only, in exactly this shape:\n"
    '{"concepts": ["concept name", ...], '
    '"edges": [{"source": "concept name", "relation": "verb phrase", '
    '"target": "concept name", "evidence": "sentence from the abstract"}, ...]}\n'
    f"At most {MAX_CONCEPTS} concepts, each a short noun phrase naming an idea, method or "
    "problem. Prefer terms the wider field already uses over anything only this paper names.\n"
    "Never a dataset, benchmark, metric or leaderboard name, and never a bare fragment that "
    'is not a concept on its own, e.g. "patch level".\n'
    "One form per concept: if the paper gives both an acronym and its expansion, pick one "
    "and use it everywhere.\n"
    'A relation is a short verb phrase, e.g. "improves", "contradicts", "builds on".\n'
    "Every edge's source and target must appear verbatim in the concepts list, and every "
    "concept must appear in at least one edge — drop any you cannot connect.\n"
    "Evidence must be quoted from the title or abstract, never paraphrased or invented.\n"
    "No prose, no markdown fences, no explanation."
)


@dataclass
class GraphResult:
    """As honest as IngestResult: what landed, and what silently didn't."""

    papers_processed: int = 0
    concepts_created: int = 0
    edges_created: int = 0
    papers_failed: int = 0  # API error, unparseable JSON or invalid shape on that one paper


def normalize(name: str) -> str:
    """Concept identity. Casing and whitespace are surface noise, not different nodes."""
    return " ".join(name.split()).lower()


@dataclass
class RelatedConcept:
    id: int
    name: str
    normalized: str
    hops: int  # 0 is the concept asked about


@dataclass
class RelatedEdge:
    """One paper's claim about two concepts. Provenance is the payload, not decoration."""

    source: str
    relation: str
    target: str
    evidence: str
    paper_id: int
    title: str
    year: int | None
    url: str | None


@dataclass
class Subgraph:
    concept: str  # as asked, not normalized
    depth: int  # after clamping to MAX_DEPTH
    # False means no such concept — an empty neighbourhood is a result, not an error.
    found: bool
    concepts: list[RelatedConcept]
    edges: list[RelatedEdge]


class GraphWorker:
    """Extracts concepts and relationships from papers. The orchestrator registers by `name`."""

    name = "graph"

    def run(self, session: Session, papers: Sequence[Paper], **kwargs: Any) -> GraphResult:
        result = GraphResult()
        for paper in papers:
            _extract_paper(session, paper, result)
        log.info("graph extraction: %s", result)
        return result

    # Extraction and traversal take different inputs and answer different questions, so
    # traversal gets its own method rather than a dispatch flag on run(). The Worker
    # protocol only asks for `name` + `run`; it stays satisfied.
    def traverse(self, session: Session, concept: str, depth: int = DEFAULT_DEPTH) -> Subgraph:
        return traverse(session, concept, depth)


def traverse(session: Session, concept: str, depth: int = DEFAULT_DEPTH) -> Subgraph:
    """The neighbourhood of `concept` within `depth` hops, and the claims connecting it.

    One recursive CTE walks the graph in the database; edges are then fetched for the
    reached set. Traversal is undirected — `A improves B` is a relationship of B too —
    and each returned edge carries the paper that asserted it plus the quoted evidence.
    """
    depth = min(depth, MAX_DEPTH)

    # Both directions, as one edge list the walk can follow without caring which way it points.
    steps = (
        select(
            ConceptEdge.source_concept_id.label("src"), ConceptEdge.target_concept_id.label("dst")
        )
        .union_all(
            select(
                ConceptEdge.target_concept_id.label("src"),
                ConceptEdge.source_concept_id.label("dst"),
            )
        )
        .cte("steps")
    )

    walk = (
        select(
            Concept.id.label("concept_id"),
            literal(0).label("hops"),
            array([Concept.id]).label("path"),
        )
        .where(Concept.normalized == normalize(concept))
        .cte("walk", recursive=True)
    )
    walk = walk.union_all(
        select(
            steps.c.dst.label("concept_id"),
            (walk.c.hops + 1).label("hops"),
            func.array_append(walk.c.path, steps.c.dst, type_=ARRAY(Integer)).label("path"),
        )
        .join_from(walk, steps, steps.c.src == walk.c.concept_id)
        .where(
            walk.c.hops < depth,
            # Cycle guard. Research graphs contain them ("A builds on B" and "B builds on
            # A" from two papers); without this the recursion never terminates. The depth
            # bound alone would stop it, but only after re-walking every loop.
            ~walk.c.path.any(steps.c.dst),
        )
    )

    # A concept reachable by several paths is one node, at its shortest distance.
    nearest = (
        select(walk.c.concept_id, func.min(walk.c.hops).label("hops"))
        .group_by(walk.c.concept_id)
        .subquery()
    )
    concepts = [
        RelatedConcept(id=row.id, name=row.name, normalized=row.normalized, hops=row.hops)
        for row in session.execute(
            select(Concept.id, Concept.name, Concept.normalized, nearest.c.hops)
            .join(nearest, nearest.c.concept_id == Concept.id)
            .order_by(nearest.c.hops, Concept.name)
        )
    ]
    log.info("graph traversal: %r reached %d concept(s) at depth %d", concept, len(concepts), depth)
    if not concepts:
        return Subgraph(concept=concept, depth=depth, found=False, concepts=[], edges=[])

    return Subgraph(
        concept=concept,
        depth=depth,
        found=True,
        concepts=concepts,
        edges=_edges_among(session, [c.id for c in concepts]),
    )


def _edges_among(session: Session, ids: list[int]) -> list[RelatedEdge]:
    """Every claim whose two endpoints are both inside the neighbourhood."""
    source, target = aliased(Concept), aliased(Concept)
    return [
        RelatedEdge(
            source=src_name,
            relation=edge.relation,
            target=tgt_name,
            evidence=edge.evidence,
            paper_id=paper.id,
            title=paper.title,
            year=paper.year,
            url=paper.url,
        )
        for edge, src_name, tgt_name, paper in session.execute(
            select(ConceptEdge, source.name, target.name, Paper)
            .join(source, source.id == ConceptEdge.source_concept_id)
            .join(target, target.id == ConceptEdge.target_concept_id)
            .join(Paper, Paper.id == ConceptEdge.paper_id)
            .where(
                ConceptEdge.source_concept_id.in_(ids), ConceptEdge.target_concept_id.in_(ids)
            )
            .order_by(ConceptEdge.id)
        )
    ]


def _extract_paper(session: Session, paper: Paper, result: GraphResult) -> None:
    """One paper, one transaction — so a bad extraction costs only its own rows."""
    try:
        concepts, edges = _validate(_ask_claude(paper))
        # Declare -> filter edges -> drop the unconnected -> persist. A concept nothing
        # says anything about is noise, not a node; declining to create its row here
        # never touches a row another paper's edges already earned.
        used = {end for e in edges for end in (e["source"], e["target"])}
        if orphans := concepts.keys() - used:
            log.info("graph: dropping %d unconnected concept(s): %s", len(orphans), sorted(orphans))
        concepts = {n: name for n, name in concepts.items() if n in used}
        ids = {}
        for normalized, name in concepts.items():
            concept_id, created = _concept_id(session, normalized, name)
            ids[normalized] = concept_id
            result.concepts_created += created
        for edge in edges:
            result.edges_created += _insert_edge(session, paper, ids, edge)
        session.commit()
        result.papers_processed += 1
    except MissingAPIKeyError:
        raise  # a missing key is config, not data: it fails the whole run, loudly
    except Exception as exc:
        session.rollback()
        log.warning("graph: skipping paper %s/%s: %s", paper.source, paper.external_id, exc)
        result.papers_failed += 1


def _ask_claude(paper: Paper) -> str:
    message = _client().messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        messages=[
            {"role": "user", "content": f"Title: {paper.title}\n\nAbstract: {paper.abstract}"}
        ],
    )
    return "".join(block.text for block in message.content if block.type == "text")


def _validate(raw: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    """The model proposes; this decides. Anything malformed is dropped before it reaches the DB.

    Returns concepts as {normalized: surface form} and only the edges whose endpoints
    were actually declared — an edge naming an undeclared concept would otherwise
    conjure a dangling node out of a hallucination. Raises on unparseable output.
    """
    # Models wrap JSON in ``` fences out of habit, prompt or no prompt.
    payload = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))

    concepts: dict[str, str] = {}
    for name in payload["concepts"]:
        if isinstance(name, str) and normalize(name):
            concepts.setdefault(normalize(name), name.strip())

    edges = []
    for edge in payload["edges"]:
        source, target = normalize(str(edge.get("source", ""))), normalize(str(edge.get("target", "")))
        relation = str(edge.get("relation", "")).strip()
        if source not in concepts or target not in concepts or not relation:
            log.warning("graph: dropping edge, undeclared concept or empty relation: %r", edge)
            continue
        edges.append(
            {
                "source": source,
                "target": target,
                "relation": relation,
                "evidence": str(edge.get("evidence", "")).strip(),
            }
        )
    return concepts, edges


def _concept_id(session: Session, normalized: str, name: str) -> tuple[int, bool]:
    """Insert-or-fetch by normalized form: the same concept from two papers is one row."""
    concept_id = session.execute(
        insert(Concept)
        .values(name=name, normalized=normalized)
        .on_conflict_do_nothing(constraint="uq_concepts_normalized")
        .returning(Concept.id)
    ).scalar()
    if concept_id is not None:
        return concept_id, True
    existing = session.execute(select(Concept.id).where(Concept.normalized == normalized)).scalar_one()
    return existing, False


def _insert_edge(session: Session, paper: Paper, ids: dict[str, int], edge: dict[str, str]) -> bool:
    """Re-extracting a paper re-asserts its own claims; ON CONFLICT keeps that a no-op."""
    return (
        session.execute(
            insert(ConceptEdge)
            .values(
                source_concept_id=ids[edge["source"]],
                target_concept_id=ids[edge["target"]],
                relation=edge["relation"],
                paper_id=paper.id,
                evidence=edge["evidence"],
            )
            .on_conflict_do_nothing(constraint="uq_concept_edges_claim")
            .returning(ConceptEdge.id)
        ).scalar()
        is not None
    )
