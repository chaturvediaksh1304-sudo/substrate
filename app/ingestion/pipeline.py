import logging
from dataclasses import asdict, dataclass, field

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ingestion.chunk import chunk_text
from app.ingestion.embed import embed
from app.ingestion.sources import TIMEOUT, PaperRecord, fetch_arxiv, fetch_semantic_scholar
from app.models import Chunk, Paper

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    topic: str
    papers_fetched: int = 0
    papers_persisted: int = 0
    papers_skipped: int = 0  # already ingested by an earlier run
    papers_failed: int = 0  # embedding or DB error on that one row
    chunks_persisted: int = 0
    # A source that times out returns [] and is indistinguishable from one that
    # genuinely found nothing, so report the per-source yield rather than guess.
    papers_by_source: dict[str, int] = field(default_factory=dict)
    source_errors: dict[str, str] = field(default_factory=dict)


def ingest_topic(session: Session, topic: str, limit: int) -> IngestResult:
    """Fetch, chunk, embed and persist papers for a topic. Never raises on bad input data."""
    result = IngestResult(topic=topic)
    # follow_redirects: arxiv's http:// endpoint 301s to https and httpx won't follow
    # by default, so an unredirected fetch silently yields nothing.
    client = httpx.Client(timeout=TIMEOUT, follow_redirects=True)
    # Resolved per call so tests can monkeypatch the module-level fetchers.
    for name, fetch in (("semantic_scholar", fetch_semantic_scholar), ("arxiv", fetch_arxiv)):
        try:
            papers = fetch(topic, limit, client=client)
        except Exception as exc:  # one source dying must not cost us the other's papers
            log.warning("source %s raised, continuing without it: %s", name, exc)
            result.source_errors[name] = repr(exc)
            papers = []
        result.papers_by_source[name] = len(papers)
        result.papers_fetched += len(papers)
        for paper in papers:
            _ingest_paper(session, paper, result)
    client.close()
    log.info("ingested topic %r: %s", topic, result)
    return result


def _ingest_paper(session: Session, paper: PaperRecord, result: IngestResult) -> None:
    """One paper, one transaction — so a failure rolls back only its own row."""
    try:
        paper_id = session.execute(
            insert(Paper)
            .values(**asdict(paper))
            .on_conflict_do_nothing(constraint="uq_papers_source_external_id")
            .returning(Paper.id)
        ).scalar()
        if paper_id is None:  # already present: leave its chunks alone
            session.commit()
            result.papers_skipped += 1
            return

        texts = chunk_text(f"{paper.title}\n\n{paper.abstract}")
        vectors = embed(texts)
        session.add_all(
            Chunk(paper_id=paper_id, chunk_index=i, text=text, embedding=vector)
            for i, (text, vector) in enumerate(zip(texts, vectors))
        )
        session.commit()
        result.papers_persisted += 1
        result.chunks_persisted += len(texts)
    except Exception as exc:
        session.rollback()
        log.warning("skipping paper %s/%s: %s", paper.source, paper.external_id, exc)
        result.papers_failed += 1
