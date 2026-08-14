# Substrate — Phases

Each phase is a loop: implement → verify against done-criteria → fix → re-check → proceed. Never skip ahead. Tests are written before implementation (TDD) for every phase per Rules.md.

## Phase 1: Foundation & Ingestion
- Goal: FastAPI project skeleton running locally in Docker, Postgres+pgvector configured, and a working pipeline that pulls papers from Semantic Scholar/arXiv for a given topic, chunks them, embeds them, and stores them.
- Done-criteria:
  - [ ] FastAPI app boots in Docker with a health-check endpoint
  - [ ] Postgres + pgvector schema exists and migrations run cleanly
  - [ ] Given a topic string, ingestion pipeline pulls N papers from Semantic Scholar/arXiv, chunks, embeds, and persists them
  - [ ] Tests cover ingestion + storage happy path and at least one failure case (e.g. API timeout) without crashing the batch
- Depends on: none

## Phase 2: RAG Q&A (MVP)
- Goal: A question in, cited answer out — the MVP success criterion from PRD.md. Also scaffolds the orchestrator/worker agent pattern that later phases plug into.
- Done-criteria:
  - [ ] API endpoint accepts a research question
  - [ ] Orchestrator agent receives the question and delegates to a retrieval worker agent
  - [ ] Retrieval worker embeds the question and retrieves top-k relevant chunks from the vector store
  - [ ] Retrieved chunks + question are sent to Claude and return a synthesized, cited answer
  - [ ] End-to-end test: real question → real papers ingested → coherent cited answer
- Depends on: Phase 1

## Phase 3: Knowledge Graph
- Goal: Concepts and relationships extracted from ingested papers, stored as a graph, linked across papers — implemented as a graph worker agent under the orchestrator.
- Done-criteria:
  - [ ] Graph storage integrated alongside the existing schema (`concepts` + `concept_edges` in Postgres; Neo4j dropped at Phase 3 scoping — see Architecture.md)
  - [ ] Graph worker agent extracts concepts and populates nodes/edges from ingested papers
  - [ ] Orchestrator can delegate a graph-traversal task to the graph agent and get relationships between concepts across multiple papers
- Depends on: Phase 2

## Phase 4: Gap Detection
- Goal: Identify under-connected or contradictory areas in the knowledge graph — implemented as a gap-detection worker agent under the orchestrator.
- Done-criteria:
  - [ ] Gap-detection agent flags sparse or missing connections between related concept clusters
  - [ ] Gap-detection agent flags contradictory claims across papers on the same concept
  - [ ] Output is a structured list of candidate gaps, not just raw graph stats
- Depends on: Phase 3

## Phase 5: Hypothesis Generation
- Goal: Given a detected gap, propose a testable hypothesis — implemented as a hypothesis worker agent under the orchestrator.
- Done-criteria:
  - [ ] Hypothesis agent takes a gap + supporting graph/paper context and produces a hypothesis
  - [ ] Hypothesis is specific and falsifiable, not a vague restatement of the gap
  - [ ] Test covers at least one real gap → hypothesis case reviewed for quality
- Depends on: Phase 4

## Phase 6: Experiment Design
- Goal: Turn a hypothesis into a structured experiment proposal — implemented as an experiment-design worker agent under the orchestrator.
- Done-criteria:
  - [ ] Experiment-design agent, given a hypothesis, outputs a structured experiment design (method, variables, expected outcome)
  - [ ] Output is reviewed against at least one real hypothesis from Phase 5 for usefulness
- Depends on: Phase 5

## Phase 7: Web UI
- Goal: Frontend on top of the now-stable API, using Aksh's default stack (React/Next.js/TypeScript) unless reassessed at this point.
- Done-criteria:
  - [ ] UI can submit a question and display the cited answer
  - [ ] UI can browse the knowledge graph / gaps / hypotheses / experiment proposals from earlier phases
  - [ ] Hosting decision (Docker self-hosted vs Vercel) finalized and deployed
  - [ ] Pre-launch checklist (below) complete
- Depends on: Phase 6

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
