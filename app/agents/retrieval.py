import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.embed import embed
from app.models import Chunk, Paper

log = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    """A chunk plus its paper's citation fields, so synthesis never has to re-query."""

    text: str
    # Raw cosine distance from pgvector: 0.0 = identical, 2.0 = opposite. LOWER IS BETTER.
    # Left as distance rather than converted to similarity — it is what the ORDER BY uses.
    score: float
    paper_id: int
    title: str
    authors: list[str]
    year: int | None
    url: str | None
    source: str
    external_id: str


class RetrievalWorker:
    """Top-k chunks for a question. The orchestrator registers workers by `name`."""

    name = "retrieval"

    def run(self, session: Session, question: str, k: int = 5) -> list[RetrievedChunk]:
        """Nearest k chunks by cosine distance. An empty store is [] — a result, not an error."""
        distance = Chunk.embedding.cosine_distance(embed([question])[0]).label("distance")
        rows = session.execute(
            select(Chunk.text, distance, Paper)
            .join(Paper, Paper.id == Chunk.paper_id)
            .order_by(distance)
            .limit(k)
        ).all()
        log.info("retrieval: %r matched %d chunk(s)", question, len(rows))
        return [
            RetrievedChunk(
                text=text,
                score=score,
                paper_id=paper.id,
                title=paper.title,
                authors=paper.authors,
                year=paper.year,
                url=paper.url,
                source=paper.source,
                external_id=paper.external_id,
            )
            for text, score, paper in rows
        ]
