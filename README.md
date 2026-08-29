# Substrate

An AI research assistant that takes a research question, searches and synthesizes across
academic papers, and answers with citations back to real sources.

It goes further than Q&A: it builds a concept graph across papers, finds gaps and
contradictions in that graph, proposes falsifiable hypotheses from them, and turns those into
structured experiment designs.

## Status

| Phase | | |
|---|---|---|
| 1 | Foundation & ingestion | ✅ Verified |
| 2 | RAG Q&A | ✅ Verified |
| 3 | Knowledge graph | ✅ Verified |
| 4 | Gap detection | ⚠️ Runs; assessment layer is weak |
| 5 | Hypothesis generation | ✅ Verified |
| 6 | Experiment design | ✅ Verified |
| 7 | macOS app (SwiftUI) | 🔨 Core loop built |
| 7b–9 | Web UI, auth, mobile | Not started |

The whole arc has now run end to end on real papers, on a **local model with no API key** —
question → cited answer → concept graph → gaps → hypothesis → experiment design.

From a real detected gap between `EVOR` (a code-generation retrieval system) and `aligned
visual captions`, it proposed: *"EVOR, adapted to use aligned visual captions in its knowledge
base, will show improved execution accuracy on code generation tasks involving visual
elements"* — then designed the ablation to test it, over the benchmark from EVOR's own paper.

**What ⚠️ still means:** gap detection *runs*, and its structural half is good. Two
deterministic guards decide what may bridge a gap — **ubiquity** (a concept in >15% of papers
tells you nothing about any two of its neighbours) and **fan-out** (>3 edges per paper it
appears in is one verbose abstract, not a concept). They catch opposite failures and neither
substitutes for the other. Together they took cross-paper gaps from 1 to 93 on an 82-paper
graph while removing the junk bridges.

But the layer that judges whether a gap is *interesting* is weak: across two model sizes it
rated every surviving gap 2 on a 1–3 scale, never once using 1 or 3. A weak binary filter
wearing a rating scale.

The pattern across the project: **everything deterministic works; everything left to model
judgement underperforms.** Retrieval ranking, graph traversal, the hub penalty, concept
identity, the restatement and testability guards — all verified against real data. Citation
integrity has never once been violated.

264 tests, single user, no auth. See [Phases.md](Phases.md) for the sequence and
[Memory.md](Memory.md) for live state, decisions, and known gaps.

## Quickstart

Requires Docker.

```bash
cp .env.example .env      # then paste your key into ANTHROPIC_API_KEY
docker compose up -d
curl localhost:8000/health
```

### Or run with no API key, on a local model

```bash
brew install ollama && ollama serve   # or: OLLAMA_HOST=0.0.0.0 ollama serve
ollama pull qwen2.5:7b-instruct
LLM_PROVIDER=ollama docker compose up -d api
```

`LLM_PROVIDER=ollama` swaps the Anthropic client for a local one at a single seam; every route
behaves the same, including the 503 you get when Ollama isn't running. Override `OLLAMA_MODEL`
to use a different pulled model.

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
| `POST` | `/graph/build` | Extracts concepts and typed edges from ingested papers |
| `POST` | `/graph/traverse` | `{concept, depth}` → the subgraph around a concept |
| `POST` | `/gaps` | Ranked candidate gaps: missing links and cross-paper contradictions |
| `POST` | `/hypotheses` | Gaps → specific, falsifiable hypotheses |
| `POST` | `/experiments` | Hypotheses → structured experiment designs |

`/ingest` is synchronous and reports what happened per source, including partial failures.
The model-backed routes return 503 rather than a plausible-looking non-answer when the
configured provider is unreachable or unconfigured.

## How it works

Papers come from [Semantic Scholar](https://www.semanticscholar.org/product/api) and
[arXiv](https://info.arxiv.org/help/api/index.html) — titles and abstracts for now. They're
chunked, embedded locally with [fastembed](https://github.com/qdrant/fastembed)
(`all-MiniLM-L6-v2`, 384-dim, no API cost), and stored in Postgres with
[pgvector](https://github.com/pgvector/pgvector).

A question goes to an **orchestrator**, which delegates to one of five **worker agents** —
retrieval, graph, gap-detection, hypothesis, experiment-design. Retrieval embeds the question
and pulls the nearest chunks by cosine distance; those passages plus the question go to the
model for synthesis. The knowledge graph lives in Postgres as `concepts` + `concept_edges`,
traversed with a recursive CTE — Neo4j was planned and dropped once Phase 3 was scoped, since a
second store bought nothing at this size. See [Architecture.md](Architecture.md).

Gap detection looks for **open triads** — A links to B, B links to C, nobody linked A to C —
and for **cross-paper contradictions**, where different papers assert different relations about
the same concept pair. Bridges are filtered by ubiquity and fan-out first, then a deterministic
prescore ranks what survives before any model call, so spend follows the request limit rather
than the size of the graph.

There's also a native **SwiftUI macOS client** in [`mac/`](mac) — one window, no dependencies,
built with SwiftPM.

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
- **Gap *significance* judgement is unreliable.** Two model sizes, 7B and 14B, both rated every
  surviving gap 2 on a 1–3 scale. Doubling the model bought exactly one extra rejection. A
  deterministic pre-filter is the likelier fix than a bigger model, but the obvious one —
  embedding distance between gap endpoints — is now suspect: see below.
- **Embeddings do not separate concepts at this granularity.** Measured over all 5,356 pairs in
  the live graph, `RAG` sits 0.885 from `retrieval augmented generation` while
  `hallucination`/`misinformation` sit at 0.861 and `corpus poisoning`/`adversarial attack` at
  0.874. Same-concept and different-concept pairs occupy the same range, so no threshold exists.
  MiniLM is a subword model and an acronym shares no subwords with its expansion — it places
  `RAG` closest to `cloth rag`, at 0.144. Acronym identity is handled structurally instead, by
  initials matching: 1 hit in 5,356 pairs, and it was the right one.
- Only 27 of 55 ingested papers are in the graph — `/graph/build` has no cursor, so it can only
  ever process papers 1..N.
- Claude has never been tested on any of this; there has been no API key. The ceiling of a
  frontier model on these tasks is unknown.
