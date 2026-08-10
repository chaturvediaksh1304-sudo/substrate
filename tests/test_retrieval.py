from app.agents import retrieval
from app.ingestion.embed import embed
from app.models import Chunk, Paper

# Three unrelated topics. The question below shares no wording with any of them,
# so a hit can only come from the embedding, not from lexical overlap.
PHOTOSYNTHESIS = "Chloroplasts capture photons and drive carbon fixation, storing energy as glucose."
MONETARY = "The central bank raised interest rates again to curb inflation across the eurozone."
FOLDING = "Molecular dynamics simulations predict how amino acid sequences reach a tertiary structure."


def vec(i: int) -> list[float]:
    """Deterministic 384-dim unit vector — orthogonal for distinct i, so ranking is exact."""
    v = [0.0] * 384
    v[i] = 1.0
    return v


def add_paper(session, external_id, title="A paper", authors=None, year=2020, url=None):
    paper = Paper(
        source="arxiv",
        external_id=external_id,
        title=title,
        abstract="abstract",
        authors=authors or ["A. Author"],
        year=year,
        url=url or f"http://arxiv.org/abs/{external_id}",
    )
    session.add(paper)
    session.flush()
    return paper


def add_chunk(session, paper, text, vector, index=0):
    session.add(Chunk(paper_id=paper.id, chunk_index=index, text=text, embedding=vector))
    session.flush()


# --- the test that proves retrieval is semantic, not just SELECT ... LIMIT k ---


def test_ranks_the_semantically_closest_chunk_first(db_session):
    """Real fastembed. The right chunk is inserted last, so order can't be an artifact."""
    paper = add_paper(db_session, "2401.00001")
    texts = [MONETARY, FOLDING, PHOTOSYNTHESIS]
    for i, (text, vector) in enumerate(zip(texts, embed(texts))):
        add_chunk(db_session, paper, text, vector, index=i)

    results = retrieval.RetrievalWorker().run(db_session, "How do plants turn light into food?", k=3)

    assert [r.text for r in results][0] == PHOTOSYNTHESIS
    assert results[0].score < results[1].score  # cosine distance: lower is nearer


# --- k, empty store, short store ----------------------------------------------


def test_k_is_honoured(db_session, monkeypatch):
    monkeypatch.setattr(retrieval, "embed", lambda texts: [vec(0)])
    paper = add_paper(db_session, "2401.00002")
    for i in range(5):
        add_chunk(db_session, paper, f"chunk {i}", vec(i), index=i)

    assert len(retrieval.RetrievalWorker().run(db_session, "anything", k=2)) == 2


def test_empty_store_returns_no_results(db_session, monkeypatch):
    monkeypatch.setattr(retrieval, "embed", lambda texts: [vec(0)])

    assert retrieval.RetrievalWorker().run(db_session, "anything") == []


def test_fewer_chunks_than_k_returns_what_exists(db_session, monkeypatch):
    monkeypatch.setattr(retrieval, "embed", lambda texts: [vec(0)])
    paper = add_paper(db_session, "2401.00003")
    add_chunk(db_session, paper, "only chunk", vec(0))
    add_chunk(db_session, paper, "second chunk", vec(1), index=1)

    assert len(retrieval.RetrievalWorker().run(db_session, "anything", k=5)) == 2


# --- citation metadata --------------------------------------------------------


def test_carries_its_own_papers_citation_metadata(db_session, monkeypatch):
    """Two papers, and the match belongs to the second — a bad join would report the first."""
    monkeypatch.setattr(retrieval, "embed", lambda texts: [vec(7)])
    decoy = add_paper(db_session, "2401.00004", title="Decoy", authors=["Wrong Person"], year=1999)
    add_chunk(db_session, decoy, "decoy chunk", vec(0))
    wanted = add_paper(
        db_session,
        "2401.00005",
        title="Attention Is All You Need",
        authors=["Ashish Vaswani", "Noam Shazeer"],
        year=2017,
        url="http://arxiv.org/abs/1706.03762v7",
    )
    add_chunk(db_session, wanted, "wanted chunk", vec(7))

    top = retrieval.RetrievalWorker().run(db_session, "anything", k=1)[0]

    assert top.text == "wanted chunk"
    assert top.paper_id == wanted.id
    assert top.title == "Attention Is All You Need"
    assert top.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert top.year == 2017
    assert top.url == "http://arxiv.org/abs/1706.03762v7"
    assert (top.source, top.external_id) == ("arxiv", "2401.00005")
    assert top.score == 0.0  # identical vectors


def test_worker_is_registerable_by_name():
    assert retrieval.RetrievalWorker.name == "retrieval"
