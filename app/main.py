import logging

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import SessionLocal, engine
from app.ingestion.pipeline import IngestResult, ingest_topic

app = FastAPI(title="Substrate")
log = logging.getLogger(__name__)


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
