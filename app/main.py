import logging

import anthropic
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.agents.orchestrator import Answer, Orchestrator
from app.agents.synthesis import MissingAPIKeyError
from app.db import SessionLocal, engine
from app.ingestion.pipeline import IngestResult, ingest_topic

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
