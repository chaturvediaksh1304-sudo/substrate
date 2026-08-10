FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv

COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml --extra dev

# Bake the ~90MB ONNX model into the image so first request doesn't download it.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')"

ENV PYTHONPATH=/srv PYTHONUNBUFFERED=1
COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
