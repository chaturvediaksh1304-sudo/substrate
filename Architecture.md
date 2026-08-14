# Substrate — Architecture

## Platform
API/backend-only for now. No UI in MVP. Web UI is planned for Phase 7, mobile app for Phase 9 — see PRD.md and Phases.md.

## Stack
- Frontend: none yet (Phase 7 will add React/Next.js/TypeScript per Aksh's default stack, decided at that time)
- Backend: Python + FastAPI
- Database: PostgreSQL + pgvector (papers, metadata, and embeddings in one place, minimal ops overhead). **Neo4j was planned for Phase 3 and dropped once Phase 3 was scoped** — this doc invited that flag. The knowledge graph is a `concepts` + `concept_edges` pair of tables traversed with a recursive CTE: no second container, no second driver, no two stores to keep in sync, and Phase 4 gap detection becomes a join against the papers already stored. Revisit Neo4j if multi-hop traversal at real corpus size outgrows a recursive CTE — the graph worker is the only thing that touches graph storage.
- Auth: none for MVP. OAuth (Google/GitHub) planned for Phase 8 once multi-user support is needed.
- Hosting/deploy: Docker, self-hosted, for local development now. Repo lives on GitHub as source of truth (see Rules.md — no auto-push). Production hosting for the eventual web app (Docker vs Vercel) is undecided and will be revisited at Phase 7.
- Third-party APIs/services: **Assumed, flag if wrong** —
  - Claude API for reasoning/synthesis (fits Aksh's existing stack)
  - Semantic Scholar API as primary paper source (broad coverage, free, includes citation graph data useful for Phase 3)
  - arXiv API as secondary source for preprints
  - Embedding model: **fastembed** running `sentence-transformers/all-MiniLM-L6-v2` (384-dim), decided Phase 1. Same model and same vectors as the sentence-transformers library, but ONNX rather than torch — image is 785MB instead of ~2.5GB and cold start is fast. Still open-source, still no per-call cost. Reversible in one file (`app/ingestion/embed.py`) if OpenAI embeddings prove necessary for quality.

## Agent architecture
Substrate itself is built as a multi-agent system: an **orchestrator agent** takes the incoming research question/task and delegates to **specialist worker agents**, each owning one capability:
- Retrieval agent (RAG — Phase 2)
- Graph agent (knowledge graph construction/traversal — Phase 3)
- Gap-detection agent (Phase 4)
- Hypothesis agent (Phase 5)
- Experiment-design agent (Phase 6)

The orchestrator coordinates calls between agents and assembles the final response. Each worker agent is added as its corresponding phase lands — this is not required for Phase 1–2 (retrieval agent only), but the orchestrator/worker pattern should be scaffolded from Phase 2 onward so later agents plug in without a rearchitecture.

## Folder structure
Settled in Phase 1 — see Memory.md for the live file list. Leaner than the sketch below it: routes live in `app/main.py` (split into `app/api/` at three or more), `app/models.py` is one file for two tables, and there is no `app/services/` until something needs one.

```
app/
  main.py  config.py  db.py  models.py
  ingestion/  sources.py  chunk.py  embed.py  pipeline.py
migrations/versions/
tests/
```

## Data flow
1. Ingestion pipeline pulls papers from Semantic Scholar/arXiv for a topic or on-demand for a question.
2. Papers are chunked and embedded, stored in Postgres/pgvector.
3. A research question hits the API, gets embedded, and retrieves the most relevant chunks.
4. Retrieved chunks + question go to Claude for synthesis into a cited answer.
5. (Later phases) Concepts extracted from papers populate the `concepts`/`concept_edges` graph in Postgres; gap detection and hypothesis generation reason over that graph plus the vector store.
