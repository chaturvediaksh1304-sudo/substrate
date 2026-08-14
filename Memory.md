# Substrate — Memory

Live state of the project. Updated on every file added/removed and every decision made or
changed — not at phase boundaries (`Rules.md:31`).

> **Note:** this structure is a stand-in. Aksh has a template to supply; replace this layout
> with it when it arrives, keeping the content.

**Last updated:** 2026-08-14 — corpus grown to 55 papers / 107 chunks (retrieval-augmented
generation). Retrieval verified at that scale. Phases 2 and 3 each still one criterion short,
both blocked on `ANTHROPIC_API_KEY`.

**Corpus:** 55 papers, 107 chunks, all arXiv (Semantic Scholar still 429s). Topics: retrieval
augmented generation (~50), protein folding diffusion models (5). Graph tables hold only 4
hand-seeded concepts / 3 edges from traversal testing — **not model output**; clear them before
the first real extraction run so seeded rows can't be mistaken for extracted ones.

---

## Status

| Phase | State |
|---|---|
| 1 — Foundation & Ingestion | ✅ Verified, all four done-criteria met |
| 2 — RAG Q&A (MVP) | ⚠️ Built, 4/5 verified — live end-to-end answer needs an API key |
| 3 — Knowledge graph | ⚠️ Built, all three done-criteria met — extraction *quality* unproven (no API key) |
| 4–9 | Not started |

## Current files

```
app/
  __init__.py
  main.py            GET /health (DB-backed), POST /ingest, POST /ask,
                     POST /graph/build, POST /graph/traverse
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
    claude.py        shared lazy Anthropic client + MissingAPIKeyError (moved out of
                     synthesis.py when extraction became a second consumer)
    retrieval.py     RetrievedChunk; RetrievalWorker.run(session, question, k=5)
                     cosine distance top-k, LOWER IS BETTER
    synthesis.py     synthesize(question, chunks) -> SynthesisResult; Citation.
                     Citations built server-side from chunks.
    orchestrator.py  Worker protocol; Orchestrator.answer(session, question, k=5) -> Answer;
                     Orchestrator.relate(session, concept, depth=2) -> Subgraph.
                     Defaults register RetrievalWorker + GraphWorker
    graph.py         GraphWorker.run(session, papers) -> GraphResult; LLM concept +
                     edge extraction, validated before persistence.
                     GraphWorker.traverse / traverse(session, concept, depth=2) -> Subgraph;
                     RelatedConcept, RelatedEdge; recursive CTE, both directions,
                     path-array cycle guard. DEFAULT_DEPTH=2, MAX_DEPTH=4
migrations/
  env.py  script.py.mako  versions/0001_initial.py  versions/0002_knowledge_graph.py
tests/
  __init__.py  conftest.py  test_health.py  test_sources.py  test_chunk.py  test_pipeline.py
  test_retrieval.py  test_synthesis.py  test_orchestrator.py  test_ask.py
  test_graph_extraction.py  test_graph_traversal.py
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
| 2026-08-12 | **Neo4j dropped; knowledge graph lives in Postgres** as `concepts` + `concept_edges`, traversed by recursive CTE | `Architecture.md` invited the flag once Phase 3 was scoped. No second container, no second driver, no two stores to sync; Phase 4 gap detection becomes a join against papers already stored. Revisit if multi-hop traversal outgrows a CTE — `graph.py` is the only thing touching graph storage. `Phases.md` criterion updated to match. |
| 2026-08-12 | `concepts.normalized` **unique** | Collapsing the same concept across papers into one node is the whole point — "linked across papers" fails without stable identity. |
| 2026-08-12 | Edge unique on `(source, target, relation, paper_id)` | Two papers asserting the same relation are two rows — independent corroboration is the signal Phase 4 counts. The same paper asserting it twice is a re-run, not evidence. |
| 2026-08-12 | Edges carry `paper_id` + `evidence` | Phase 4 detects contradictions *across papers*; without knowing who asserted an edge and what text backs it, that phase has nothing to reason over. |
| 2026-08-12 | Extraction asks for **JSON, validated before persistence** | Same discipline as citations: the model proposes, our code validates. An edge referencing an undeclared concept is dropped and logged, never persisted as a dangling node. |
| 2026-08-12 | **Shared Anthropic client** moved to `app/agents/claude.py` | Extraction became the second consumer, so the seam earned its own module. A pure move — no retry/config/wrapper added. `MissingAPIKeyError` stays importable from `synthesis` so `main.py` is unbroken. |
| 2026-08-13 | Traversal is **undirected** | `A improves B` is a fact about B as much as about A. A source→target-only walk silently returns half the neighbourhood. The CTE walks a `UNION ALL` of the edge table with its endpoints swapped. |
| 2026-08-13 | Cycle guard is a **visited-path array** (`NOT dst = ANY(path)`) plus the depth bound | Two papers asserting `A builds on B` and `B builds on A` is normal in research; an unguarded recursive CTE never returns. The depth bound alone would terminate but only after re-walking every loop. Cost: the CTE enumerates simple paths, so `MAX_DEPTH` is 4 — raise it only with real numbers. |
| 2026-08-13 | `GraphWorker` gets a **separate `traverse()` method**, not a dispatch flag on `run()` | Extraction takes papers and calls Claude; traversal takes a concept name and touches only Postgres. One entry point would need a mode kwarg branching on a string. The `Worker` protocol only requires `name` + `run`, and both stay satisfied. |
| 2026-08-13 | Unknown concept → **200 with `found=False`**, never 404 | Same reading as `/ask`'s empty retrieval: "no such concept in this corpus" is a correct answer to the question asked. 503 stays reserved for a missing key or an unreachable Claude, which only `/graph/build` can hit. |
| 2026-08-13 | Traversal is **two queries**, not one | The recursive CTE finds the neighbourhood; a second query fetches the edges among it with their paper join. Folding both into one statement would not let an edge-less concept report `found=True`. Two bounded queries, not N+1. |
| 2026-08-13 | Routes stayed in `main.py` at five routes | Contradicts the 2026-08-10 "split at three or more" note. `main.py` is still ~100 lines of thin handlers; the split buys nothing yet. Revisit when Phase 4/5 add theirs. |

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
- **`k` counts chunks, not papers.** A paper split into two chunks can occupy two of the top-k
  slots, so `k=5` may surface as few as 2–3 distinct papers — narrower evidence than the number
  suggests. Observed live: a chunking question returned 4 chunks from only 2 papers. Synthesis
  already groups citations per paper, so the answer isn't wrong, just thinner-sourced than it
  looks. Consider retrieving by distinct paper, or over-fetching then deduping, if Phase 4's
  gap detection wants breadth.
- **~4% corpus noise**: arXiv's `all:` matching pulled 2 augmented-*reality* papers into a
  retrieval-augmented-*generation* query. Left in deliberately — real literature searches are
  noisy, and gap detection should be robust to it. A quoted phrase query would tighten it.
- **Concept extraction has never run against the live model** — same `ANTHROPIC_API_KEY` gap as
  Phase 2. Extraction *quality* is therefore unproven; only its plumbing is tested.
  `POST /graph/build` correctly returns 503 today.
- **The dev DB's 4 concepts / 3 edges were hand-seeded via SQL** (2026-08-13) over real papers
  11, 12 and 15, purely so `POST /graph/traverse` could be verified live while `/graph/build`
  is blocked on the key. They are not model output. Delete them once a real build runs.
- Traversal enumerates **simple paths**, so a hub concept with high degree costs more at
  `MAX_DEPTH=4` than the row count suggests. Fine at 10 papers; measure before raising the cap.
- **iCloud bind-mount flakiness**: the container intermittently throws
  `OSError: [Errno 35] Resource deadlock avoided` reading a `.py`/`.toml` that iCloud has
  evicted to the cloud. Not caused by any code change — it killed runs on an unmodified repo.
  Workaround before a test run: `find . -name '*.py' | while read f; do cat "$f" >/dev/null; done`
- Scratch database `substrate_migrate` still exists on the `db` container from migration
  testing. Harmless; dropping it needs approval per `Rules.md`.
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
curl -X POST localhost:8000/graph/build -H 'Content-Type: application/json' \
  -d '{"limit":10}'
curl -X POST localhost:8000/graph/traverse -H 'Content-Type: application/json' \
  -d '{"concept":"<concept>","depth":2}'
```
