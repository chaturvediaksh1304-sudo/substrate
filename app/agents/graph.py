import json
import logging
from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.agents.claude import MissingAPIKeyError, _client
from app.config import settings
from app.models import Concept, ConceptEdge, Paper

log = logging.getLogger(__name__)

# A title + abstract yields a handful of concepts and edges; this is room to spare.
MAX_TOKENS = 2000

SYSTEM = (
    "You extract a knowledge graph from a paper's title and abstract.\n"
    "Reply with JSON only, in exactly this shape:\n"
    '{"concepts": ["concept name", ...], '
    '"edges": [{"source": "concept name", "relation": "verb phrase", '
    '"target": "concept name", "evidence": "sentence from the abstract"}, ...]}\n'
    "Concepts are short noun phrases the paper is actually about.\n"
    'A relation is a short verb phrase, e.g. "improves", "contradicts", "builds on".\n'
    "Every edge's source and target must appear verbatim in the concepts list.\n"
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


class GraphWorker:
    """Extracts concepts and relationships from papers. The orchestrator registers by `name`."""

    name = "graph"

    def run(self, session: Session, papers: Sequence[Paper], **kwargs: Any) -> GraphResult:
        result = GraphResult()
        for paper in papers:
            _extract_paper(session, paper, result)
        log.info("graph extraction: %s", result)
        return result


def _extract_paper(session: Session, paper: Paper, result: GraphResult) -> None:
    """One paper, one transaction — so a bad extraction costs only its own rows."""
    try:
        concepts, edges = _validate(_ask_claude(paper))
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
