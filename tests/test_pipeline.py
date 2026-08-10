from sqlalchemy import func, select, text

from app.ingestion import pipeline
from app.ingestion.sources import PaperRecord
from app.models import Chunk, Paper

S2_PAPER = PaperRecord(
    source="semantic_scholar",
    external_id="204e3073870fae3d05bcbc2f6a8e263d9b72e776",
    title="Attention is All you Need",
    abstract="The dominant sequence transduction models are based on complex recurrent networks.",
    authors=["Ashish Vaswani", "Noam M. Shazeer"],
    year=2017,
    url="https://www.semanticscholar.org/paper/204e3073870fae3d05bcbc2f6a8e263d9b72e776",
)

ARXIV_PAPER = PaperRecord(
    source="arxiv",
    external_id="1810.04805v2",
    title="BERT: Pre-training of Deep Bidirectional Transformers",
    abstract="We introduce a new language representation model called BERT.",
    authors=["Jacob Devlin"],
    year=2018,
    url="http://arxiv.org/abs/1810.04805v2",
)


def fake_embed(texts):
    """Deterministic 384-length vectors — the real model is exercised in its own test."""
    return [[0.1] * 384 for _ in texts]


def patch_sources(monkeypatch, s2, arxiv):
    monkeypatch.setattr(pipeline, "fetch_semantic_scholar", s2)
    monkeypatch.setattr(pipeline, "fetch_arxiv", arxiv)


def returns(papers):
    def fetch(topic, limit, client=None):
        return papers

    return fetch


def raises(exc):
    def fetch(topic, limit, client=None):
        raise exc

    return fetch


def counts(session):
    return (
        session.execute(select(func.count()).select_from(Paper)).scalar(),
        session.execute(select(func.count()).select_from(Chunk)).scalar(),
    )


# --- happy path ---------------------------------------------------------------


def test_happy_path_persists_papers_and_chunks(db_session, monkeypatch):
    patch_sources(monkeypatch, returns([S2_PAPER]), returns([ARXIV_PAPER]))
    monkeypatch.setattr(pipeline, "embed", fake_embed)

    result = pipeline.ingest_topic(db_session, "transformers", 5)

    papers = db_session.execute(select(Paper).order_by(Paper.source)).scalars().all()
    assert [p.external_id for p in papers] == [ARXIV_PAPER.external_id, S2_PAPER.external_id]
    assert papers[1].authors == ["Ashish Vaswani", "Noam M. Shazeer"]

    paper_count, chunk_count = counts(db_session)
    assert result.papers_fetched == 2
    assert result.papers_persisted == paper_count == 2
    assert result.papers_skipped == 0
    assert result.papers_failed == 0
    assert result.chunks_persisted == chunk_count
    assert chunk_count >= 2  # every paper contributes at least one chunk
    assert result.papers_by_source == {"semantic_scholar": 1, "arxiv": 1}
    assert result.source_errors == {}

    dims = db_session.execute(text("SELECT DISTINCT vector_dims(embedding) FROM chunks")).scalars().all()
    assert dims == [384]


def test_real_embedder_end_to_end(db_session, monkeypatch):
    """The one test that runs fastembed for real — proves the integration, not the mock."""
    patch_sources(monkeypatch, returns([S2_PAPER]), returns([]))

    result = pipeline.ingest_topic(db_session, "transformers", 5)

    assert result.papers_persisted == 1
    chunk = db_session.execute(select(Chunk)).scalars().one()
    assert len(chunk.embedding) == 384
    assert any(v != 0 for v in chunk.embedding)  # a real model, not a zero vector


# --- idempotency --------------------------------------------------------------


def test_rerun_does_not_duplicate(db_session, monkeypatch):
    patch_sources(monkeypatch, returns([S2_PAPER]), returns([ARXIV_PAPER]))
    monkeypatch.setattr(pipeline, "embed", fake_embed)

    pipeline.ingest_topic(db_session, "transformers", 5)
    before = counts(db_session)

    again = pipeline.ingest_topic(db_session, "transformers", 5)

    assert counts(db_session) == before
    assert again.papers_persisted == 0
    assert again.papers_skipped == 2
    assert again.chunks_persisted == 0


# --- Phase 1 failure criterion: one source down, the other still lands ---------


def test_empty_source_does_not_block_the_other(db_session, monkeypatch):
    """fetch_semantic_scholar degrades to [] on timeout/429 — arXiv must still persist."""
    patch_sources(monkeypatch, returns([]), returns([ARXIV_PAPER]))
    monkeypatch.setattr(pipeline, "embed", fake_embed)

    result = pipeline.ingest_topic(db_session, "transformers", 5)

    assert counts(db_session)[0] == 1
    assert result.papers_persisted == 1
    assert result.papers_by_source == {"semantic_scholar": 0, "arxiv": 1}


def test_raising_source_does_not_block_the_other(db_session, monkeypatch):
    patch_sources(monkeypatch, raises(RuntimeError("semantic scholar exploded")), returns([ARXIV_PAPER]))
    monkeypatch.setattr(pipeline, "embed", fake_embed)

    result = pipeline.ingest_topic(db_session, "transformers", 5)

    assert [p.external_id for p in db_session.execute(select(Paper)).scalars()] == [ARXIV_PAPER.external_id]
    assert result.papers_persisted == 1
    assert "semantic scholar exploded" in result.source_errors["semantic_scholar"]
    assert result.papers_by_source["semantic_scholar"] == 0


def test_both_sources_down_is_an_empty_result_not_an_exception(db_session, monkeypatch):
    patch_sources(monkeypatch, raises(RuntimeError("boom")), returns([]))

    result = pipeline.ingest_topic(db_session, "transformers", 5)

    assert counts(db_session) == (0, 0)
    assert result.papers_fetched == 0
    assert result.papers_persisted == 0


# --- one bad paper mid-batch --------------------------------------------------


def test_bad_paper_does_not_kill_the_batch(db_session, monkeypatch):
    bad, good_a, good_b = S2_PAPER, ARXIV_PAPER, PaperRecord(
        source="arxiv",
        external_id="1706.03762v7",
        title="Attention Is All You Need",
        abstract="The dominant sequence transduction models are based on complex recurrent networks.",
        authors=["Ashish Vaswani"],
        year=2017,
        url="http://arxiv.org/abs/1706.03762v7",
    )
    patch_sources(monkeypatch, returns([bad]), returns([good_a, good_b]))

    def flaky_embed(texts):
        if texts[0].startswith(bad.title):
            raise RuntimeError("onnx session died")
        return fake_embed(texts)

    monkeypatch.setattr(pipeline, "embed", flaky_embed)

    result = pipeline.ingest_topic(db_session, "transformers", 5)

    assert sorted(p.external_id for p in db_session.execute(select(Paper)).scalars()) == sorted(
        [good_a.external_id, good_b.external_id]
    )
    assert result.papers_fetched == 3
    assert result.papers_persisted == 2
    assert result.papers_failed == 1
    assert counts(db_session)[1] == result.chunks_persisted


def test_ingest_endpoint_returns_result(db_session, monkeypatch):
    """POST /ingest wires the pipeline to HTTP — the route's own session is the app's."""
    from fastapi.testclient import TestClient

    from app import main

    patch_sources(monkeypatch, returns([S2_PAPER]), returns([]))
    monkeypatch.setattr(pipeline, "embed", fake_embed)
    monkeypatch.setattr(main, "SessionLocal", lambda: db_session)

    body = TestClient(main.app).post("/ingest", json={"topic": "transformers", "limit": 3}).json()

    assert body["papers_persisted"] == 1
    assert body["papers_by_source"] == {"semantic_scholar": 1, "arxiv": 0}


def test_ingest_endpoint_rejects_absurd_limit():
    from fastapi.testclient import TestClient

    from app import main

    assert TestClient(main.app).post("/ingest", json={"topic": "x", "limit": 10_000}).status_code == 422
