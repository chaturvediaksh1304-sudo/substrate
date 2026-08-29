# Substrate — Phases

Each phase is a loop: implement → verify against done-criteria → fix → re-check → proceed. Never skip ahead. Tests are written before implementation (TDD) for every phase per Rules.md.

## Phase 1: Foundation & Ingestion
- Goal: FastAPI project skeleton running locally in Docker, Postgres+pgvector configured, and a working pipeline that pulls papers from Semantic Scholar/arXiv for a given topic, chunks them, embeds them, and stores them.
- Done-criteria:
  - [x] FastAPI app boots in Docker with a health-check endpoint
  - [x] Postgres + pgvector schema exists and migrations run cleanly
  - [x] Given a topic string, ingestion pipeline pulls N papers from Semantic Scholar/arXiv, chunks, embeds, and persists them
  - [x] Tests cover ingestion + storage happy path and at least one failure case (e.g. API timeout) without crashing the batch
- Depends on: none

## Phase 2: RAG Q&A (MVP)
- Goal: A question in, cited answer out — the MVP success criterion from PRD.md. Also scaffolds the orchestrator/worker agent pattern that later phases plug into.
- Done-criteria:
  - [x] API endpoint accepts a research question
  - [x] Orchestrator agent receives the question and delegates to a retrieval worker agent
  - [x] Retrieval worker embeds the question and retrieves top-k relevant chunks from the vector store
  - [x] Retrieved chunks + question are sent to Claude and return a synthesized, cited answer
  - [x] End-to-end test: real question → real papers ingested → coherent cited answer
        **Met 2026-08-26** via `qwen2.5:7b-instruct` on Ollama. "How is RAG evaluated?" returned
        a correct summary of Ragas citing the real Ragas paper. Style is weak (annotated-
        bibliography voice, one self-contradiction); the pipeline and citation mapping are correct.
- Depends on: Phase 1

## Phase 3: Knowledge Graph
- Goal: Concepts and relationships extracted from ingested papers, stored as a graph, linked across papers — implemented as a graph worker agent under the orchestrator.
- Done-criteria:
  - [x] Graph storage integrated alongside the existing schema (`concepts` + `concept_edges` in Postgres; Neo4j dropped at Phase 3 scoping — see Architecture.md)
  - [x] Graph worker agent extracts concepts and populates nodes/edges from ingested papers
  - [x] Orchestrator can delegate a graph-traversal task to the graph agent and get relationships between concepts across multiple papers
- Depends on: Phase 2

## Phase 4: Gap Detection
- Goal: Identify under-connected or contradictory areas in the knowledge graph — implemented as a gap-detection worker agent under the orchestrator.
- Done-criteria:
  - [x] Gap-detection agent flags sparse or missing connections between related concept clusters
  - [x] Gap-detection agent flags contradictory claims across papers on the same concept
  - [x] Output is a structured list of candidate gaps, not just raw graph stats
- Depends on: Phase 3

## Phase 5: Hypothesis Generation
- Goal: Given a detected gap, propose a testable hypothesis — implemented as a hypothesis worker agent under the orchestrator.
- Done-criteria:
  - [x] Hypothesis agent takes a gap + supporting graph/paper context and produces a hypothesis
  - [x] Hypothesis is specific and falsifiable, not a vague restatement of the gap
  - [x] Test covers at least one real gap → hypothesis case reviewed for quality
        **Met 2026-08-26.** Real gap (EVOR ↔ aligned visual captions) → "EVOR, adapted to use
        aligned visual captions in its knowledge base, will show improved execution accuracy on
        code generation tasks involving visual elements." Specific, falsifiable, grounded in two
        real papers. A second hypothesis from the same run was weaker — a comparison rather than
        a mechanism.
- Depends on: Phase 4

## Phase 6: Experiment Design
- Goal: Turn a hypothesis into a structured experiment proposal — implemented as an experiment-design worker agent under the orchestrator.
- Done-criteria:
  - [x] Experiment-design agent, given a hypothesis, outputs a structured experiment design (method, variables, expected outcome)
  - [x] Output is reviewed against at least one real hypothesis from Phase 5 for usefulness
        **Met 2026-08-26.** A real ablation: two EVOR variants, with and without aligned visual
        captions, over EVOR-BENCH, measuring execution accuracy — naming a benchmark that comes
        from the source paper itself. Controls listed, expected and discriminating outcomes both
        concrete. A researcher could run it.
- Depends on: Phase 5

## Phase 7: macOS app
Reassessed at Phase 7, as this phase always allowed. A **native SwiftUI macOS app** replaces the
web UI here; the web UI keeps its scope and moves to Phase 7b. iOS follows later — `SubstrateCore`
is already platform-agnostic so that is a views-only job.

- Goal: Native macOS client on top of the now-stable API.
- Done-criteria:
  - [x] App submits a question and displays the cited answer
  - [x] Every state the API distinguishes is shown distinctly — answered, nothing found,
        backend unavailable (503, server's own message), unreachable, unexpected
  - [ ] Browse the knowledge graph / gaps / hypotheses / experiment proposals
  - [ ] Verified against a live answer (**blocked**: needs `ANTHROPIC_API_KEY`)
- Depends on: Phase 6

## Phase 7b: Web UI
- Goal: Frontend on top of the API, React/Next.js/TypeScript.
- Done-criteria:
  - [ ] UI can submit a question and display the cited answer
  - [ ] UI can browse the knowledge graph / gaps / hypotheses / experiment proposals
  - [ ] Hosting decision (Docker self-hosted vs Vercel) finalized and deployed
  - [ ] Pre-launch checklist (below) complete — **web-only**; it covers robots.txt, meta
        descriptions, sitemaps and social sharing, none of which apply to a Mac app, so it
        travels with this phase rather than being dropped
- Depends on: Phase 7

### Pre-launch checklist
- [ ] Custom 404 page
- [ ] CTA above the fold
- [ ] Internal links
- [ ] Thank-you page
- [ ] Breadcrumbs
- [ ] Case studies
- [ ] 5 FAQs
- [ ] Response time promise
- [ ] Sticky mobile CTA
- [ ] robots.txt
- [ ] Unique page titles
- [ ] Meta descriptions
- [ ] Social sharing
- [ ] Maps + directions
- [ ] Real reviews
- [ ] Alt text on images
- [ ] Local schema
- [ ] Privacy policy page
- [ ] Google Analytics
- [ ] Team photo (all backend capability should exist before UI wraps it)

## Phase 8: Auth & Multi-user
- Goal: Multiple users, each with their own research sessions.
- Done-criteria:
  - [ ] OAuth (Google/GitHub) implemented
  - [ ] User-scoped data access enforced (no cross-user leakage)
- Depends on: Phase 7

## Phase 9: Mobile App
- Goal: Mobile client, last in sequence, once the web app is proven.
- Done-criteria:
  - [ ] Mobile app consumes the same API as the web app
  - [ ] Core flow (ask question → get answer) works on mobile
- Depends on: Phase 8
