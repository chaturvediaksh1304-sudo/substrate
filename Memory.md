# Substrate — Memory

Live state of the project. Updated on every file added/removed and every decision made or
changed — not at phase boundaries (`Rules.md:31`).

> **Note:** this structure is a stand-in. Aksh has a template to supply; replace this layout
> with it when it arrives, keeping the content.

**Last updated:** 2026-08-12 — README added, repo pushed public. Phase 2 still 4 of 5
verified; criterion 5 blocked on `ANTHROPIC_API_KEY`.

---

## Status

| Phase | State |
|---|---|
| 1 — Foundation & Ingestion | ✅ Verified, all four done-criteria met |
| 2 — RAG Q&A (MVP) | ⚠️ Built, 4/5 verified — live end-to-end answer needs an API key |
| 3–9 | Not started |

## Current files

```
app/
  __init__.py
  main.py            GET /health (DB-backed), POST /ingest, POST /ask
  config.py          DATABASE_URL required (fails loud); EMBEDDING_DIM=384;
                     ANTHROPIC_API_KEY optional at startup, required at use; ANTHROPIC_MODEL
  db.py              engine, SessionLocal, Base
  models.py          Paper, Chunk — unique (source, external_id); Chunk.embedding Vector(384)
  ingestion/
    __init__.py
    sources.py       PaperRecord; fetch_semantic_scholar(), fetch_arxiv()
    chunk.py         chunk_text(text, max_chars=1000, overlap=100)
    embed.py         embed(texts) -> 384-dim vectors; lazy fastembed singleton
    pipeline.py      ingest_topic(session, topic, limit) -> IngestResult
  agents/
    __init__.py
    retrieval.py     RetrievedChunk; RetrievalWorker.run(session, question, k=5)
                     cosine distance top-k, LOWER IS BETTER
    synthesis.py     synthesize(question, chunks) -> SynthesisResult; Citation;
                     MissingAPIKeyError. Citations built server-side from chunks.
    orchestrator.py  Worker protocol; Orchestrator.answer(session, question, k=5) -> Answer
migrations/
  env.py  script.py.mako  versions/0001_initial.py
tests/
  __init__.py  conftest.py  test_health.py  test_sources.py  test_chunk.py  test_pipeline.py
  test_retrieval.py  test_synthesis.py  test_orchestrator.py  test_ask.py
Dockerfile  docker-compose.yml  pyproject.toml  alembic.ini
.env.example  .gitignore  .dockerignore
```

Docs: `README.md`, `PRD.md`, `Architecture.md`, `Rules.md`, `Phases.md`, `Design.md`, `Memory.md`.

Repo: https://github.com/chaturvediaksh1304-sudo/substrate — public, `main`, pushed 2026-08-12.

## Dependencies

Runtime: fastapi, uvicorn[standard], sqlalchemy, alembic, psycopg[binary], pgvector,
pydantic-settings, httpx, fastembed, anthropic (0.121.0, added Phase 2). Dev: pytest.

Rejected on purpose: `respx` (httpx ships `MockTransport`), `pytest-asyncio` (stack is fully
synchronous), `feedparser` (stdlib `xml.etree.ElementTree` parses arXiv's Atom).

## Decisions

| Date | Decision | Why |
|---|---|---|
| 2026-08-10 | **fastembed (ONNX) instead of sentence-transformers**, same `all-MiniLM-L6-v2`, 384-dim | Identical vectors without torch — 785MB image vs ~2.5GB, fast cold start. Deviates from `Architecture.md`, which invited the flag. Reversible in `app/ingestion/embed.py` alone. |
| 2026-08-10 | **Titles + abstracts only**, no PDF full text | Both APIs return them for every hit; no PDF deps, no mixed-fidelity corpus. Full text is a later add. |
| 2026-08-10 | Stack is **fully synchronous** | Sync SQLAlchemy + `httpx.Client` + sync handlers. Async buys nothing at this scale and costs test plumbing. |
| 2026-08-10 | Routes live in `app/main.py`, no `app/api/` package | Two routes. Split at three or more. |
| 2026-08-10 | **Commit per paper** during ingest, not per batch | Required for "one bad row doesn't kill the batch" to actually hold. |
| 2026-08-10 | **Idempotent ingest** via `ON CONFLICT (source, external_id) DO NOTHING` | Ingest gets re-run constantly in development. |
| 2026-08-10 | `limit` is **per source**, not total | Matches how both fetchers read it; `limit:5` can yield 10 papers. |
| 2026-08-10 | Chunk at **1000 chars** | Fits MiniLM's 256-token window, so nothing is silently truncated at embed time. A long abstract becomes 2 chunks — preferred over truncation. |
| 2026-08-10 | **No ivfflat/hnsw index** on `embedding` yet | Nothing to tune against. `ponytail:` note in `models.py` names the upgrade path. |
| 2026-08-10 | Git initialized, local commits only | No remote, no push, per `Rules.md`. |
| 2026-08-10 | **anthropic SDK** over raw httpx for Claude calls | Retries, typed errors, streaming, tool use — Phases 3–6 add five more agents that would each re-implement them. |
| 2026-08-10 | Default model `claude-sonnet-5`, one `settings.ANTHROPIC_MODEL` line | Good synthesis quality at sensible cost/latency. Swap without touching code. |
| 2026-08-10 | **`ANTHROPIC_API_KEY` optional at startup, fatal at use** | `Rules.md` calls for hard-failing on missing keys at startup, but that would take down a working `/health` and `/ingest` over a key only `/ask` needs. Synthesis raises `MissingAPIKeyError`; `/ask` returns 503. Deliberate reading of the rule — revisit if you'd rather the app refuse to boot. |
| 2026-08-10 | **Citations constructed server-side, never parsed from model prose** | An answer citing papers that don't exist is the "silently wrong output" `Rules.md` hard-fails on. The model cites by index; indices map back to real `RetrievedChunk`s; out-of-range indices are dropped and logged. |
| 2026-08-10 | Sources numbered **per paper**, not per chunk | Two passages from one paper would otherwise burn two citation indices on the same reference. |
| 2026-08-10 | Empty retrieval → **no Claude call**, `Answer.found = False`, HTTP 200 | An honest "nothing relevant found" is a correct answer, not an error, and saves a pointless API call. |
| 2026-08-10 | Anthropic API errors **propagate** → 503 | Degrading into a plausible-looking answer would be worse than failing. |

## Known gaps / open items

- **Semantic Scholar returns 429 on every live call** — no API key set. The degrade path works
  (logs, returns `[]`, arXiv still delivers) and the S2 parse/persist path is unit-tested, but
  **no S2 paper has traversed the live network path**. Effectively single-source today. A free
  key resolves it; matters more at Phase 3, which wants S2's citation graph.
- `sources.py` cannot distinguish "source timed out" from "source found nothing" — both are `[]`.
  `IngestResult.source_errors` only fills when a source raises past its own handling.
- No test for `/health`'s degraded branch (needs the DB torn down mid-run).
- Skipped papers get no chunk re-check — an already-present paper is assumed to have its chunks.
- Starlette deprecation warning on every test run: `httpx` in `TestClient`, wants `httpx2`.
- **Phase 2 criterion 5 is unverified**: no `ANTHROPIC_API_KEY` is set, so no live question has
  ever produced a real cited answer. `tests/test_ask.py` holds the end-to-end test, correctly
  skipping rather than failing. Run it and a real `POST /ask` once the key lands.
- `log.error` from app code doesn't surface in `docker compose logs` — uvicorn configures no
  root handler. Affects `pipeline.py`, `retrieval.py`, `synthesis.py`, `main.py`. One logging
  config line fixes it whenever it starts costing debugging time.

## Fixes worth remembering

- **arXiv 301-redirects `http://export.arxiv.org` to HTTPS.** httpx doesn't follow redirects by
  default and `raise_for_status()` doesn't flag a 301, so the empty body hit the XML parser and
  `fetch_arxiv` degraded to `[]` — silently, on every call. Fixed at the root in
  `sources.py:13` (`http://` → `https://`), not just in the pipeline's injected client.

## Verification commands

```bash
docker compose up -d
curl localhost:8000/health
docker compose run --rm api pytest -q
curl -X POST localhost:8000/ingest -H 'Content-Type: application/json' \
  -d '{"topic":"<topic>","limit":5}'
curl -X POST localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question":"<question>","k":5}'
```
