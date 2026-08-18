import logging
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from sqlalchemy.orm import Session

from app.agents.gaps import RANK_LIMIT, CandidateGap, GapWorker
from app.agents.graph import DEFAULT_DEPTH, GraphWorker, Subgraph
from app.agents.hypothesis import HYPOTHESIS_LIMIT, Hypothesis, HypothesisWorker
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
class Hypotheses:
    """Hypotheses off the top gaps, plus how many gaps were behind them.

    `gaps_considered` is what makes an empty list readable: 0 means the graph held no gaps
    to work from, and anything higher means gaps were found and every proposal was refused
    (a restatement, a bad shape, an unusable reply). Same job `Answer.found` does — an
    honest non-result that a caller can tell apart from a real one without parsing prose.
    """

    gaps_considered: int
    hypotheses: list[Hypothesis]


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
        default = [RetrievalWorker(), GraphWorker(), GapWorker(), HypothesisWorker()]
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

    def hypothesize(self, session: Session, limit: int = HYPOTHESIS_LIMIT) -> Hypotheses:
        """Delegate gap detection, then one hypothesis per gap. Both hops go via the registry.

        The entry point is the corpus, not a hand-built gap: a `CandidateGap` carries concept
        ids, paper rows and a model's assessment, so a caller cannot supply one — it comes out
        of `find_gaps`. `limit` is therefore one knob for both hops, capping the gaps assessed
        and the hypotheses attempted, because assessing gaps nobody will hypothesize over is
        spend with nothing to show for it.

        A gap the model produces nothing usable from is dropped, not raised on — that is
        `generate_hypothesis`' contract, and `gaps_considered` keeps it visible.
        """
        gaps = self.find_gaps(session, limit)
        found = [self.workers["hypothesis"].run(session, gap) for gap in gaps]
        return Hypotheses(
            gaps_considered=len(gaps),
            hypotheses=[hypothesis for hypothesis in found if hypothesis is not None],
        )
