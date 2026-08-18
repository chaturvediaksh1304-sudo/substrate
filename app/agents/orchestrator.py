import logging
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from sqlalchemy.orm import Session

from app.agents.gaps import RANK_LIMIT, CandidateGap, GapWorker
from app.agents.graph import DEFAULT_DEPTH, GraphWorker, Subgraph
from app.agents.retrieval import RetrievalWorker
from app.agents.synthesis import Citation, synthesize

log = logging.getLogger(__name__)

NOTHING_FOUND = (
    "No relevant papers were found for this question. Ingest papers on this topic and ask again."
)


class Worker(Protocol):
    """A specialist agent. Phase 3+ agents (graph, gap, hypothesis) just need these two."""

    name: str

    def run(self, session: Session, question: str, **kwargs: Any) -> Any: ...


@dataclass
class Answer:
    question: str
    answer: str
    citations: list[Citation]
    chunks_retrieved: int
    # False means retrieval came back empty and Claude was never called — an honest
    # non-answer, distinguishable from a synthesized one without parsing the prose.
    found: bool


class Orchestrator:
    """Takes the question, delegates to workers by name, assembles the answer."""

    def __init__(
        self,
        workers: Sequence[Worker] | None = None,
        synthesize_fn: Callable[..., Any] = synthesize,
    ):
        default = [RetrievalWorker(), GraphWorker(), GapWorker()]
        self.workers = {worker.name: worker for worker in workers or default}
        self.synthesize = synthesize_fn

    def answer(self, session: Session, question: str, k: int = 5) -> Answer:
        chunks = self.workers["retrieval"].run(session, question, k=k)
        if not chunks:
            log.info("orchestrator: nothing retrieved for %r, skipping synthesis", question)
            return Answer(
                question=question,
                answer=NOTHING_FOUND,
                citations=[],
                chunks_retrieved=0,
                found=False,
            )
        result = self.synthesize(question, chunks)
        return Answer(
            question=question,
            answer=result.answer,
            citations=result.citations,
            chunks_retrieved=len(chunks),
            found=True,
        )

    def relate(self, session: Session, concept: str, depth: int = DEFAULT_DEPTH) -> Subgraph:
        """Delegate a graph traversal. No synthesis: the subgraph is the answer."""
        return self.workers["graph"].traverse(session, concept, depth)

    def find_gaps(self, session: Session, limit: int = RANK_LIMIT) -> list[CandidateGap]:
        """Delegate gap detection. No synthesis: the ranked list is the answer."""
        return self.workers["gaps"].run(session, limit)
