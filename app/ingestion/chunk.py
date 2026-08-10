def chunk_text(text: str, max_chars: int = 1000, overlap: int = 100) -> list[str]:
    """Split text into overlapping windows. Empty/whitespace-only text yields no chunks.

    1000 chars ~= the 256-token window of the 384-dim MiniLM embedder, so a chunk
    is never silently truncated at embed time; 100 keeps a sentence straddling a
    boundary present on both sides.
    """
    # ponytail: naive character split — cuts mid-word. Phase 1 is titles+abstracts,
    # so most inputs are 1-2 chunks. Go sentence-aware when full-text PDFs land.
    text = text.strip()
    step = max(max_chars - overlap, 1)  # overlap >= max_chars would never advance
    chunks = []
    for i in range(0, len(text), step):
        chunks.append(text[i : i + max_chars])
        if i + max_chars >= len(text):
            break  # remainder is already inside this chunk
    return chunks
