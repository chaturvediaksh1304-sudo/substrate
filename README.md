# Substrate

An AI research assistant that takes a research question, searches and synthesizes across
academic papers, and answers with citations back to real sources.

The longer goal is the full research loop — connect concepts across papers, surface gaps and
contradictions in the literature, form hypotheses, and design experiments to test them.
Today it does the first half of that: ingestion and cited Q&A.

## Status

| Phase | | |
|---|---|---|
| 1 | Foundation & ingestion | ✅ Verified |
| 2 | RAG Q&A | ⚠️ Built; the live `/ask` path needs an API key to verify |
| 3 | Knowledge graph (Neo4j) | Not started |
| 4 | Gap detection | Not started |
| 5 | Hypothesis generation | Not started |
| 6 | Experiment design | Not started |
| 7–9 | Web UI, auth, mobile | Not started |

Pre-MVP. Backend only, no UI, no auth, single user. See [Phases.md](Phases.md) for the full
sequence and [Memory.md](Memory.md) for current state, decisions, and known gaps.

## Quickstart

Requires Docker.

```bash
cp .env.example .env      # then paste your key into ANTHROPIC_API_KEY
docker compose up -d
curl localhost:8000/health
```

Ingest papers on a topic, then ask about them:

```bash
curl -X POST localhost:8000/ingest -H 'Content-Type: application/json' \
  -d '{"topic":"protein folding diffusion models","limit":10}'

curl -X POST localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question":"How are diffusion models applied to protein structure?","k":5}'
```

`/ask` returns the answer plus structured citations, each pointing at a paper actually in the
store. `found: false` means retrieval turned up nothing relevant — an honest non-answer rather
than an invented one.

## API

| | | |
|---|---|---|
| `GET` | `/health` | Liveness, including a real database check |
| `POST` | `/ingest` | `{topic, limit}` → pulls papers, chunks, embeds, persists. Idempotent. |
| `POST` | `/ask` | `{question, k}` → cited answer |

`/ingest` is synchronous and reports what happened per source, including partial failures.
`/ask` returns 503 rather than a plausible-looking non-answer if Claude is unreachable or the
API key is missing.

## How it works

Papers come from [Semantic Scholar](https://www.semanticscholar.org/product/api) and
[arXiv](https://info.arxiv.org/help/api/index.html) — titles and abstracts for now. They're
chunked, embedded locally with [fastembed](https://github.com/qdrant/fastembed)
(`all-MiniLM-L6-v2`, 384-dim, no API cost), and stored in Postgres with
[pgvector](https://github.com/pgvector/pgvector).

A question goes to an **orchestrator**, which delegates retrieval to a **retrieval worker**
that embeds the question and pulls the nearest chunks by cosine distance. Question and
passages then go to Claude for synthesis. Later phases add graph, gap-detection, hypothesis,
and experiment-design workers under the same orchestrator — see [Architecture.md](Architecture.md).

**Citations are built server-side from the retrieved chunks, never parsed out of the model's
prose.** The model may only cite by index; indices map back to real papers, and anything
out of range is dropped and logged. An answer cannot cite a paper that isn't in the store.

Ingestion and retrieval degrade rather than crash — one source timing out doesn't stop the
other, and one bad paper doesn't kill a batch. Missing keys and upstream failures fail loudly
instead, on the principle that silently wrong output is worse than no output.

## Development

```bash
docker compose run --rm api pytest -q
docker compose run --rm api alembic upgrade head
```

Python 3.12, FastAPI, SQLAlchemy + Alembic, fully synchronous. Tests run against a separate
`substrate_test` database and never hit the network; the one live end-to-end test skips
without an API key.

## Known gaps

- Semantic Scholar rate-limits unauthenticated callers hard, so in practice the corpus is
  arXiv-only today. A free API key fixes it — it matters most at Phase 3, which wants S2's
  citation graph.
- Titles and abstracts only, no full text.
- No vector index yet (sequential scan); fine at this corpus size, revisit when it isn't.
