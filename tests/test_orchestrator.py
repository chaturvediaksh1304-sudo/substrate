from app.agents.orchestrator import Orchestrator
from app.agents.retrieval import RetrievalWorker, RetrievedChunk
from app.agents.synthesis import Citation, SynthesisResult

CHUNK = RetrievedChunk(
    text="The Transformer replaces recurrence with attention.",
    score=0.1,
    paper_id=1,
    title="Attention Is All You Need",
    authors=["Ashish Vaswani"],
    year=2017,
    url="http://arxiv.org/abs/1706.03762v7",
    source="arxiv",
    external_id="1706.03762v7",
)
CITATION = Citation(
    index=1,
    title="Attention Is All You Need",
    authors=["Ashish Vaswani"],
    year=2017,
    url="http://arxiv.org/abs/1706.03762v7",
    external_id="1706.03762v7",
)


class StubWorker:
    """Stands in for RetrievalWorker and records how the orchestrator called it."""

    name = "retrieval"

    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.calls = []

    def run(self, session, question, k=5):
        self.calls.append((question, k))
        return self.chunks


class StubSynthesis:
    def __init__(self, result=None):
        self.result = result or SynthesisResult(answer="Synthesized.", citations=[CITATION])
        self.calls = []

    def __call__(self, question, chunks):
        self.calls.append((question, chunks))
        return self.result


# --- criterion 2: the orchestrator delegates, it does not inline retrieval ---


def test_delegates_to_the_worker_registered_as_retrieval():
    worker = StubWorker([CHUNK])
    synthesize = StubSynthesis()

    Orchestrator(workers=[worker], synthesize_fn=synthesize).answer(None, "What is attention?", k=3)

    assert worker.calls == [("What is attention?", 3)]


def test_the_real_retrieval_worker_is_registered_by_default():
    assert isinstance(Orchestrator().workers["retrieval"], RetrievalWorker)


# --- happy path ---


def test_assembles_worker_output_and_synthesis_output():
    worker = StubWorker([CHUNK])
    synthesize = StubSynthesis()

    answer = Orchestrator(workers=[worker], synthesize_fn=synthesize).answer(None, "q")

    assert synthesize.calls == [("q", [CHUNK])]
    assert answer.question == "q"
    assert answer.answer == "Synthesized."
    assert answer.citations == [CITATION]
    assert answer.chunks_retrieved == 1
    assert answer.found is True


# --- degrade where it's honest to: nothing retrieved is a real answer ---


def test_no_chunks_skips_synthesis_entirely():
    synthesize = StubSynthesis()

    answer = Orchestrator(workers=[StubWorker([])], synthesize_fn=synthesize).answer(None, "q")

    assert synthesize.calls == []  # no pointless API call
    assert answer.found is False
    assert answer.citations == []
    assert answer.chunks_retrieved == 0
    assert "no relevant" in answer.answer.lower()
