import logging

import anthropic
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.agents.experiment import EXPERIMENT_LIMIT, MAX_EXPERIMENTS
from app.agents.gaps import RANK_LIMIT, CandidateGap
from app.agents.graph import DEFAULT_DEPTH, MAX_DEPTH, GraphResult, Subgraph
from app.agents.hypothesis import HYPOTHESIS_LIMIT
from app.agents.orchestrator import Answer, Experiments, Hypotheses, Orchestrator
from app.agents.synthesis import MissingAPIKeyError
from app.db import SessionLocal, engine
from app.ingestion.pipeline import IngestResult, ingest_topic
from app.models import Paper

app = FastAPI(title="Substrate")
log = logging.getLogger(__name__)
orchestrator = Orchestrator()


@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        log.warning("health check: database unreachable: %s", exc)
        return {"status": "degraded", "database": "down"}
    return {"status": "ok", "database": "up"}


class IngestRequest(BaseModel):
    topic: str
    # 100 is both APIs' practical page size; beyond that a caller wants pagination.
    limit: int = Field(default=10, ge=1, le=100)


@app.post("/ingest")
def ingest(request: IngestRequest) -> IngestResult:
    # Sync on purpose: the whole stack is sync, and ingestion is short enough to
    # answer inline. Background it when a topic takes longer than a request should.
    with SessionLocal() as session:
        return ingest_topic(session, request.topic, request.limit)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, pattern=r"\S")
    # 20 chunks is already more context than an answer needs; beyond that is a different feature.
    k: int = Field(default=5, ge=1, le=20)


@app.post("/ask")
def ask(request: AskRequest) -> Answer:
    with SessionLocal() as session:
        try:
            return orchestrator.answer(session, request.question, request.k)
        except MissingAPIKeyError as exc:
            # A missing key is a hard failure (Rules.md): never a 200 with an empty answer.
            log.error("ask: %s", exc)
            raise HTTPException(503, "ANTHROPIC_API_KEY is not configured; /ask is unavailable")
        except anthropic.APIError as exc:
            log.error("ask: Claude call failed: %s", exc)
            raise HTTPException(503, "Claude is unavailable; the question was not answered")


class GraphBuildRequest(BaseModel):
    # Same ceiling as /ingest: past 100 papers this wants backgrounding, not a bigger limit.
    limit: int = Field(default=10, ge=1, le=100)


@app.post("/graph/build")
def graph_build(request: GraphBuildRequest) -> GraphResult:
    """Extract concepts and relationships from papers already ingested."""
    with SessionLocal() as session:
        papers = session.execute(select(Paper).order_by(Paper.id).limit(request.limit)).scalars().all()
        try:
            return orchestrator.workers["graph"].run(session, papers)
        except MissingAPIKeyError as exc:
            log.error("graph build: %s", exc)
            raise HTTPException(
                503, "ANTHROPIC_API_KEY is not configured; /graph/build is unavailable"
            )
        except anthropic.APIError as exc:
            log.error("graph build: Claude call failed: %s", exc)
            raise HTTPException(503, "Claude is unavailable; the graph was not built")


class TraverseRequest(BaseModel):
    concept: str = Field(min_length=1, pattern=r"\S")
    depth: int = Field(default=DEFAULT_DEPTH, ge=1, le=MAX_DEPTH)


@app.post("/graph/traverse")
def graph_traverse(request: TraverseRequest) -> Subgraph:
    # No Claude, no 503: an unknown concept is an empty subgraph with found=False.
    with SessionLocal() as session:
        return orchestrator.relate(session, request.concept, request.depth)


class GapsRequest(BaseModel):
    # One Claude call per gap assessed, so this is a spend cap, not a page size. 50 is
    # what either search hands over at most; past that a caller wants a batch job.
    limit: int = Field(default=RANK_LIMIT, ge=1, le=50)


@app.post("/gaps")
def find_gaps(request: GapsRequest) -> list[CandidateGap]:
    """Rank candidate gaps in the knowledge graph. An empty graph is [], not an error."""
    with SessionLocal() as session:
        try:
            return orchestrator.find_gaps(session, request.limit)
        except MissingAPIKeyError as exc:
            log.error("gaps: %s", exc)
            raise HTTPException(503, "ANTHROPIC_API_KEY is not configured; /gaps is unavailable")
        except anthropic.APIError as exc:
            log.error("gaps: Claude call failed: %s", exc)
            raise HTTPException(503, "Claude is unavailable; gaps were not detected")


class HypothesesRequest(BaseModel):
    # Two Claude calls per gap here (assess, then propose), so the ceiling is RANK_LIMIT
    # rather than /gaps' 50: same spend, half the gaps.
    limit: int = Field(default=HYPOTHESIS_LIMIT, ge=1, le=RANK_LIMIT)


@app.post("/hypotheses")
def hypothesize(request: HypothesesRequest) -> Hypotheses:
    """Hypothesize over the top gaps in the knowledge graph.

    No gap, or no proposal that survived validation, is a 200 with an empty list —
    `gaps_considered` says which of the two it was.
    """
    with SessionLocal() as session:
        try:
            return orchestrator.hypothesize(session, request.limit)
        except MissingAPIKeyError as exc:
            log.error("hypotheses: %s", exc)
            raise HTTPException(
                503, "ANTHROPIC_API_KEY is not configured; /hypotheses is unavailable"
            )
        except anthropic.APIError as exc:
            log.error("hypotheses: Claude call failed: %s", exc)
            raise HTTPException(503, "Claude is unavailable; no hypotheses were generated")


class ExperimentsRequest(BaseModel):
    # Three Claude calls per gap here (assess, propose, then design), so the ceiling drops
    # again: 5 x 3 stays under /hypotheses' 10 x 2, which stays under /gaps' 50 x 1.
    limit: int = Field(default=EXPERIMENT_LIMIT, ge=1, le=MAX_EXPERIMENTS)


@app.post("/experiments")
def design_experiments(request: ExperimentsRequest) -> Experiments:
    """Design an experiment per hypothesis, off the top gaps in the knowledge graph.

    Empty is a 200 three different ways, and the counts say which: no gap at all, no
    hypothesis that survived, or no design that survived.
    """
    with SessionLocal() as session:
        try:
            return orchestrator.design_experiments(session, request.limit)
        except MissingAPIKeyError as exc:
            log.error("experiments: %s", exc)
            raise HTTPException(
                503, "ANTHROPIC_API_KEY is not configured; /experiments is unavailable"
            )
        except anthropic.APIError as exc:
            log.error("experiments: Claude call failed: %s", exc)
            raise HTTPException(503, "Claude is unavailable; no experiments were designed")
