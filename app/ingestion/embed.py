from fastembed import TextEmbedding

MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # 384-dim, matches settings.EMBEDDING_DIM

_model: TextEmbedding | None = None


def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts to 384-dim vectors. The only place fastembed is named — swap it here.

    The model is a module-level singleton: constructing it loads ~90MB of ONNX, too
    slow to do at import time (every unrelated test would pay it) or per call.
    """
    global _model
    if _model is None:
        _model = TextEmbedding(MODEL)
    return [vector.tolist() for vector in _model.embed(texts)]
