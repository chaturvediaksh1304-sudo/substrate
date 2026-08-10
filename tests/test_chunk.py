from app.ingestion.chunk import chunk_text


def test_empty_input_returns_nothing():
    assert chunk_text("") == []
    assert chunk_text("   \n\t  ") == []


def test_short_input_is_one_chunk():
    text = "A short abstract about transformers."
    assert chunk_text(text) == [text]


def test_input_exactly_max_chars_is_one_chunk():
    text = "x" * 1000
    assert chunk_text(text, max_chars=1000, overlap=100) == [text]


def test_long_input_splits_with_overlap():
    text = "".join(str(i % 10) for i in range(2500))
    chunks = chunk_text(text, max_chars=1000, overlap=100)

    assert len(chunks) == 3
    assert chunks[0] == text[0:1000]
    assert chunks[1] == text[900:1900]
    assert chunks[2] == text[1800:2500]
    assert chunks[0][-100:] == chunks[1][:100]  # overlap actually overlaps
    assert text.endswith(chunks[-1])


def test_overlap_terminates_and_covers_everything():
    # overlap >= max_chars would make a naive loop never advance
    text = "y" * 5000
    chunks = chunk_text(text, max_chars=100, overlap=100)
    assert 0 < len(chunks) <= 5000
    assert all(len(c) <= 100 for c in chunks)
