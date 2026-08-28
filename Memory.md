# Substrate — Memory

Live state of the project. Updated on every file added/removed and every decision made or
changed — not at phase boundaries (`Rules.md:31`).

> **Note:** this structure is a stand-in. Aksh has a template to supply; replace this layout
> with it when it arrives, keeping the content.

**Last updated:** 2026-08-26 — **local-model provider added.** `LLM_PROVIDER=ollama` swaps the
Anthropic client for `app/agents/ollama.py` at the single `claude._client()` seam, so Substrate
runs with no `ANTHROPIC_API_KEY`. Six call sites and five routes unchanged in behaviour. Backend:
**229 tests pass, 1 skips**, eight routes.

**Phase 7 (macOS app) core loop built.** `mac/` holds a native
SwiftUI client: `SubstrateCore` (Foundation only, 6 tests) + `SubstrateMac` (views).

**Phase 6 built: the backend arc is structurally complete.**
Experiment design, `ExperimentWorker`, `POST /experiments`. 215 tests pass, 1 skips; eight
routes; five worker agents under the orchestrator. Every LLM-free half is verified live; every
Claude path is plumbing-tested only, still awaiting `ANTHROPIC_API_KEY`.

**Corpus:** 55 papers, 107 chunks, all arXiv (Semantic Scholar still 429s). Topics: retrieval
augmented generation (~50), protein folding diffusion models (5). Graph tables hold only 4
hand-seeded concepts / 3 edges from traversal testing — **not model output**; clear them before
the first real extraction run so seeded rows can't be mistaken for extracted ones.

---

## Status

| Phase | State |
|---|---|
| 1 — Foundation & Ingestion | ✅ Verified, all four done-criteria met |
| 2 — RAG Q&A (MVP) | ✅ **All 5 criteria met 2026-08-26** — first real cited answers, via local Ollama |
| 3 — Knowledge graph | ✅ Built and run for real — 27 papers, 104 concepts, 89 edges, 0 orphans, 5 cross-paper concepts |
| 4 — Gap detection | ⚠️ Run for real; hub artifacts fixed (82%→0%). Assessment layer still weak — see the model experiment below |
| 5 — Hypothesis generation | ✅ **All 3 criteria met 2026-08-26** — real gap → specific falsifiable hypothesis |
| 6 — Experiment design | ✅ **Both criteria met 2026-08-26** — real hypothesis → runnable ablation over EVOR-BENCH |
| 7 — macOS app | 🔨 Core loop built and visually verified; browse-graph/gaps screens and a live answer still pending |
| 7b — Web UI | Not started; keeps the web-only pre-launch checklist |
| 8–9 | Not started. Phase 9 (mobile) reuses `SubstrateCore` unchanged — see Design.md for the UI standards |

**Phase 4 is split into 4 parts**, ordered so the LLM-free part lands first:
1. ✅ Structural gap detection — open triads, pure SQL, verifiable without a key
2. ✅ Contradiction detection across papers — SQL candidates (no key) + Claude judge
3. ✅ Gap ranking + structured output — deterministic prescore (no key) + Claude assessment
4. ✅ `GapWorker` under the orchestrator + `/gaps` route + end-to-end

## Current files

```
app/
  __init__.py
  main.py            GET /health (DB-backed), POST /ingest, POST /ask,
                     POST /graph/build, POST /graph/traverse, POST /gaps,
                     POST /hypotheses, POST /experiments
                     — eight routes; the "split at three" decision below is overdue
  config.py          DATABASE_URL required (fails loud); EMBEDDING_DIM=384;
                                      ANTHROPIC_API_KEY optional at startup, required at use; ANTHROPIC_MODEL;
                     LLM_PROVIDER (anthropic|ollama, Literal so bad values fail at boot);
                     OLLAMA_BASE_URL; OLLAMA_MODEL
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
    claude.py        the single LLM seam: _client() returns an anthropic.Anthropic or an
                     OllamaClient per LLM_PROVIDER, cached per provider in _CLIENTS.
                     MissingAPIKeyError (moved out of synthesis.py when extraction became
                     a second consumer; only the anthropic branch can raise it).
                     unavailable(exc, consequence) -> the 503 detail routes use.
    ollama.py        OllamaClient/_Messages/Message/TextBlock — duck-typed to the one method
                     the six call sites use. POSTs {base}/api/chat, maps system= to a
                     system-role message and max_tokens to options.num_predict, substitutes
                     OLLAMA_MODEL for the caller's Anthropic model. Failures raise
                     anthropic.APIError so existing excepts keep degrading to 503.
    retrieval.py     RetrievedChunk; RetrievalWorker.run(session, question, k=5)
                     cosine distance top-k, LOWER IS BETTER
    synthesis.py     synthesize(question, chunks) -> SynthesisResult; Citation.
                     Citations built server-side from chunks.
    orchestrator.py  Worker protocol; Orchestrator.answer(session, question, k=5) -> Answer;
                     Orchestrator.relate(session, concept, depth=2) -> Subgraph;
                     Orchestrator.find_gaps(session, limit=10) -> [CandidateGap];
                     Orchestrator.hypothesize(session, limit=3) -> Hypotheses;
                     Orchestrator.design_experiments(session, limit=2) -> Experiments.
                     Defaults register RetrievalWorker + GraphWorker + GapWorker
                     + HypothesisWorker + ExperimentWorker
    gaps.py          find_open_triads(session, min_papers=1, limit=50) -> [StructuralGap];
                     one SQL self-join over an undirected edge view. No LLM.
                     find_conflicting_claims(session, limit=50) -> [ClaimConflict];
                     Claim; ordered pair, >=2 papers AND >=2 relations. No LLM.
                     judge_contradictions([ClaimConflict]) -> [Contradiction];
                     one Claude call per candidate, JSON verdict validated against
                     the real rows before a Contradiction is built.
                     rank_gaps(session, limit=10) -> [CandidateGap]; CandidateGap,
                     GapPaper. _candidates() gathers both signals, hydrates paper
                     titles in one query, prescores deterministically — no LLM.
                     GapWorker.run(session, limit=10) -> [CandidateGap]; name="gaps".
    hypothesis.py    generate_hypothesis(session, gap) -> Hypothesis | None; Hypothesis
                     (statement, manipulation, measurement, predicted_effect, falsifier,
                     papers from rows). restates_gap()/novel_terms() — deterministic
                     restatement guard, no LLM. HypothesisWorker.run(session, gap).
    experiment.py    design_experiment(session, hypothesis) -> ExperimentDesign | None;
                     ExperimentDesign (method, manipulated, measured, controlled,
                     expected_outcome, discriminating_outcome, papers from rows).
                     shared_terms()/measures_something_else() — deterministic
                     testability guard, no LLM. ExperimentWorker.run(session, hypothesis).
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
  test_graph_extraction.py  test_graph_traversal.py  test_gaps.py  test_contradictions.py
  test_gap_ranking.py  test_gaps_route.py  test_hypothesis.py  test_hypothesis_route.py
  test_experiment.py  test_experiment_route.py  test_ollama.py
Dockerfile  docker-compose.yml  pyproject.toml  alembic.ini
.env.example  .gitignore  .dockerignore
```

Docs: `README.md`, `PRD.md`, `Architecture.md`, `Rules.md`, `Phases.md`, `Design.md`, `Memory.md`.

Repo: https://github.com/chaturvediaksh1304-sudo/substrate — public, `main`, pushed 2026-08-12.

## macOS app — `mac/` (Phase 7)

```
mac/
  Package.swift          swift-tools-version 6.3, platforms [.macOS(.v26)],
                         .defaultIsolation(MainActor.self), dependencies: []
  Sources/SubstrateCore/  Foundation ONLY — reused unchanged by a later iOS app
    Models.swift          Answer, Citation, AskResult (5 cases)
    SubstrateClient.swift ask(_:k:) async -> AskResult; never throws
  Sources/SubstrateMac/
    SubstrateApp.swift    @main, 1040x760 default, 960x640 min, hidden title bar
    ChatView.swift        the one screen; transcript of question/response turns
    DesignSystem/         Colors, Typography, Spacing — no hex literal in any view
    Components/           Eyebrow, Card (the only things used more than once)
  Tests/SubstrateCoreTests/ClientTests.swift   Swift Testing, URLProtocol stub, 6 tests
  Substrate-icon.png     1254x1254 source art; bundle.sh resamples it into a 10-size
                         AppIcon.icns via sips + iconutil (both ship with macOS)
  scripts/bundle.sh      swift build -c release -> Substrate.app, incl. the icon
```

**Build/verify:** `cd mac && swift build && swift test`, then `./scripts/bundle.sh && open Substrate.app`.

**`screencapture` is blocked** — this process has no Screen Recording permission, so the live
window cannot be captured. Visual verification instead goes through a throwaway `ImageRenderer`
harness built from copies of the real view files (`ScrollView`→`VStack`, `TextField`→`Text`, the
two things ImageRenderer cannot lay out). Granting Terminal Screen Recording would remove the
workaround.

## Skills available (installed globally, 2026-08-25)

122 skills in `~/.claude/skills`, none project-local. **Nothing to install or configure** —
they activate on description match. The value is knowing which apply here, not the inventory.

**Useful to Substrate now (backend, stack-agnostic):**

| Skill | Why here |
|---|---|
| `tdd` | `Rules.md` mandates test-first for every phase; this is the reference for what makes a test worth keeping |
| `codebase-design` | Deep-module vocabulary — relevant to the overdue `app/api/` split |
| `domain-modeling` | Concept/gap/hypothesis vocabulary is load-bearing across Phases 3–6; also covers `CONTEXT.md` and ADRs |
| `diagnosing-bugs` | Diagnosis loop for hard bugs and perf regressions |
| `writing-for-agents` | Writing docs *for* agents — directly applicable to this file and `CLAUDE.md` |
| `research` | Investigate against primary sources, capture as Markdown in-repo |
| `grilling` | Stress-test a plan or decision — e.g. the open client-stack question |
| `writing-guidelines` (agent-skills) | Prose/doc audit; not installed yet |

**Phase 7, if the client is web/React:** `design-taste-frontend`, `apple-design`,
`apple-liquid-glass`, `emil-design-eng`, `animate`, `review-animations`,
`find-animation-opportunities`, `improve-animations`, `animation-vocabulary`, `ask-sonner`,
`pick-ui-library`, `prototype`, `stitch-design-taste`, `redesign-existing-projects`,
`image-to-code`, `imagegen-frontend-web`, plus the `vercel:*` plugin skills.

**Phase 7, if the client is native macOS:** **`write-swift`** (Swift 6 — value types,
data-race safety, concurrency, Swift Testing, ARC), `apple-design` (principles, web-worded),
`emil-design-eng` (craft philosophy). Plus the iOS/device skills for Phase 9:
`ios-qa`, `ios-fix`, `ios-design-review`, `animate-expo`, `imagegen-frontend-mobile`.

**`aso`** (alexszczurek/before-skills) installed 2026-08-25 — iOS App Store listing
optimization: name, subtitle, keyword field, screenshots, icon, ratings, featuring
nominations. **No bearing on Substrate until Phase 9** (mobile app, last in sequence). Partial
overlap with a Mac App Store listing if the macOS client ships there rather than direct
download — the skill is iOS-specific (Liquid Glass icons, iOS screenshot indexing, App Store
tags), so treat it as adjacent, not applicable.

**Verified 2026-08-25** against Aksh's install log: all 63 skills from `mattpocock/skills`
(37), `Leonxlnx/taste-skill` (13), `emilkowalski/skills` (12), `vercel-labs/skills`
(`find-skills`), and `playwright-cli` are present in `~/.claude/skills`. Nothing missing.

**Three name collisions from those installs, worth knowing:**
- `prototype` — emilkowalski's overwrote mattpocock's. The live one builds multiple UI variants
  behind a visual picker; mattpocock's throwaway-prototype version is gone.
- `retro` — mattpocock's overwrote gstack's Claude Code skill (the installer said so).
- `code-review` — mattpocock's skill now sits alongside the built-in `/code-review` command.

**Attribution** (by content; no provenance manifest on disk): mattpocock — `tdd`,
`codebase-design`, `domain-modeling`, `diagnosing-bugs`, `prototype`, `research`, `grilling`,
`writing-for-agents`, `wizard`, `setup-pre-commit`, `pick-ui-library`, `ask-matt`,
`improve-codebase-architecture`, `setup-ts-deep-modules`. emilkowalski — `animate`,
`animate-expo`, `animation-vocabulary`, `apple-design`, `ask-sonner`, `emil-design-eng`,
`review-animations`, `find-animation-opportunities`, `improve-animations`, `write-swift`.
Leonxlnx/taste-skill — `design-taste-frontend`, `design-taste-frontend-v1`. gstack — the
`ship`/`review`/`qa`/`spec`/`browse` family. vercel-labs — via the plugin, not this folder.

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
| 2026-08-26 | **Ollama failures are raised as `anthropic.APIError`**, not a new shared error type | Six modules and five routes already `except anthropic.APIError` and turn it into a 503. Raw httpx errors would escape as unhandled 500s — losing the degradation discipline and breaking the Mac app's `unavailable` state. The alternative (a shared `LLMError` + fifteen widened excepts) is a bigger diff for the same behaviour. Cost: `anthropic` stays a dependency even with no key, and the type name is now a slight lie. Constructed in one place, `ollama._error`. |
| 2026-08-26 | **`_client()` is the whole provider switch** — no registry, no ABC, no plugin loader | Every LLM call already funnels through it, and the existing tests monkeypatch `_client` per module. One `if` beats an abstraction with two implementations. |
| 2026-08-26 | **`OLLAMA_MODEL` substituted inside the adapter**, not at the call sites | The call sites pass `settings.ANTHROPIC_MODEL` and are provider-agnostic by design (and tested that way). Only the adapter knows it's talking to Ollama, so only the adapter can name an Ollama model. The `model=` kwarg is accepted and discarded. |
| 2026-08-26 | **`OLLAMA_BASE_URL` defaults to `localhost`, docker-compose overrides with `host.docker.internal`** | `localhost` is correct when the API runs on the host (uvicorn directly). Inside the api container `localhost` is the container, so compose supplies the value that works there. One default per context, both overridable by env. |
| 2026-08-26 | Default local model **`qwen2.5:7b-instruct`** | ~4.7GB at Q4_K_M on an 18GB M3 Pro, 32k context, and the strongest JSON/instruction-following in the 7-8B class — which is what this codebase asks for: graph extraction, contradiction judging, gap assessment, hypothesis and experiment design all parse strict JSON. |
| 2026-08-26 | Route 503 details now carry **the provider name and the upstream message** | The old strings hardcoded "Claude", which misnames the failure under Ollama and hides Ollama's own actionable message ("run `ollama pull …`"). `MissingAPIKeyError` details now come from the exception, so the wording is accurate for whichever provider raised it — and byte-identical to before on the anthropic path. |
| 2026-08-10 | Default model `claude-sonnet-5`, one `settings.ANTHROPIC_MODEL` line | Good synthesis quality at sensible cost/latency. Swap without touching code. |
| 2026-08-10 | **`ANTHROPIC_API_KEY` optional at startup, fatal at use** | `Rules.md` calls for hard-failing on missing keys at startup, but that would take down a working `/health` and `/ingest` over a key only `/ask` needs. Synthesis raises `MissingAPIKeyError`; `/ask` returns 503. Deliberate reading of the rule — revisit if you'd rather the app refuse to boot. |
| 2026-08-10 | **Citations constructed server-side, never parsed from model prose** | An answer citing papers that don't exist is the "silently wrong output" `Rules.md` hard-fails on. The model cites by index; indices map back to real `RetrievedChunk`s; out-of-range indices are dropped and logged. |
| 2026-08-10 | Sources numbered **per paper**, not per chunk | Two passages from one paper would otherwise burn two citation indices on the same reference. |
| 2026-08-10 | Empty retrieval → **no Claude call**, `Answer.found = False`, HTTP 200 | An honest "nothing relevant found" is a correct answer, not an error, and saves a pointless API call. |
| 2026-08-10 | Anthropic API errors **propagate** → 503 | Degrading into a plausible-looking answer would be worse than failing. |
| 2026-08-13 | **Neo4j dropped; knowledge graph lives in Postgres** as `concepts` + `concept_edges`, traversed by recursive CTE | `Architecture.md` invited the flag once Phase 3 was scoped. No second container, no second driver, no two stores to sync; Phase 4 gap detection becomes a join against papers already stored. Revisit if multi-hop traversal outgrows a CTE — `graph.py` is the only thing touching graph storage. `Phases.md` criterion updated to match. |
| 2026-08-13 | `concepts.normalized` **unique** | Collapsing the same concept across papers into one node is the whole point — "linked across papers" fails without stable identity. |
| 2026-08-13 | Edge unique on `(source, target, relation, paper_id)` | Two papers asserting the same relation are two rows — independent corroboration is the signal Phase 4 counts. The same paper asserting it twice is a re-run, not evidence. |
| 2026-08-13 | Edges carry `paper_id` + `evidence` | Phase 4 detects contradictions *across papers*; without knowing who asserted an edge and what text backs it, that phase has nothing to reason over. |
| 2026-08-15 | Phase 4 **split into 4 parts, structural detection first** | It's the only part of gap detection needing no Claude call, so it lands fully verified instead of adding to the stack of unproven LLM behaviour. |
| 2026-08-15 | Gap signal is the **open triad** (A–B, B–C, no A–C) | The classic literature-based-discovery pattern: the literature connects both concepts to a common one but nobody connected them to each other. One signal done properly beats a suite of graph metrics. |
| 2026-08-15 | `min_papers` defaults to **1** | Cross-paper support is a *ranking* input for part 3, not a precondition — a stricter default would silently return nothing on a sparse graph. Callers opt into strictness. |
| 2026-08-15 | Gap pairs keyed by `least(id)/greatest(id)` | (A,C) and (C,A) are the same gap. Ordering the pair by concept id is what makes dedup work, and the paper aggregation relies on that symmetry. |
| 2026-08-17 | Contradiction candidates are **`>=2 distinct papers` AND `>=2 distinct relations`** on one pair | Same paper twice is one verbose paper; same relation twice is corroboration, the opposite signal. The two counts together are exactly "some two edges differ in both paper and relation" — no third clause needed. |
| 2026-08-17 | Candidates keyed on the **ordered** pair, unlike part 1's undirected triads | "A improves B" and "B improves A" are different claims. Cost: a disagreement phrased in opposite directions is missed — `ponytail:` note in `ClaimConflict` names the fix (union the reversed edge list in as context) if extraction turns out to phrase claims both ways. |
| 2026-08-17 | **One Claude call per candidate**, not one batched call | Batching saves tokens and loses the isolation `Rules.md` asks for: one malformed reply would take the whole batch down instead of one pair. |
| 2026-08-17 | `Contradiction` **wraps its `ClaimConflict`**; the model supplies only verdict + reasoning | Same discipline as citations and edges. The model names paper ids; ids are resolved against the candidate's own claims, and a verdict naming an unknown paper is dropped and logged rather than fabricated into a row. |
| 2026-08-25 | **vercel-labs/agent-skills adopted** — https://github.com/vercel-labs/agent-skills | Aksh's call. Eight skills; `npx skills add vercel-labs/agent-skills`. **Not installed** (needs approval + reload). Two things this changed: its `web-design-guidelines` skill is the *same standard* as the separately-adopted `web-interface-guidelines/command.md`, so the skill supersedes fetching the raw doc; and `react-best-practices` is already in this session via the Vercel plugin. `writing-guidelines` is the only skill across all four adopted resources usable on Substrate **today** — it audits docs/prose, and this repo has six markdown docs. |
| 2026-08-25 | **The client-stack decision now gates four adopted standards** | Six of agent-skills' eight skills are React-specific; the Vercel rules and Impeccable's detectors assume HTML/CSS. Under native SwiftUI only awesome-design-md and `writing-guidelines` survive. Recorded in Design.md as a table. Settle the stack before adopting more tooling. |
| 2026-08-25 | **awesome-design-md adopted as the design-language source** — https://github.com/VoltAgent/awesome-design-md | Aksh's call. 73 ready-made `DESIGN.md` files reverse-engineered from real sites, in Google Stitch's spec (theme, palette + hex, type hierarchy, components, spacing, elevation, do/don't, responsive, agent prompts). Seeds our `DESIGN.md` rather than defining the brand — adopt one wholesale and Substrate looks like that company. **Most portable of the three standards**: color/type/spacing/elevation survive a native SwiftUI port; only the responsive and CSS-component sections are web-bound. |
| 2026-08-25 | **`DESIGN.md` has three would-be writers** — this repo's `Design.md`, Impeccable's `init`, and a pasted awesome-design-md file | Same file on case-insensitive macOS. Intent doesn't conflict; overwrite risk does. Agreed order: seed from awesome-design-md, refine with Impeccable, keep the standards sections at the top. Back up before running `init`. |
| 2026-08-25 | **Phase 7 is a native SwiftUI macOS app**; web UI becomes Phase 7b | Aksh's call. Xcode 26.6 / Swift 6.3.3 already present, zero new toolchain. `Phases.md` always allowed reassessing at Phase 7. The web-only pre-launch checklist travels with 7b rather than being dropped. |
| 2026-08-25 | **SwiftPM + `bundle.sh`, no `.xcodeproj`** | An Xcode project would need XcodeGen/Tuist (new toolchain) or hand-written pbxproj (brittle). `swift build`/`swift test` work headlessly; ~40 lines assemble the `.app`. |
| 2026-08-25 | **`SubstrateCore` split from `SubstrateMac`** | Core imports Foundation only, so the later iOS app is a views-only job. One extra target now. |
| 2026-08-25 | **`AskResult` has 5 cases, not 4** | `unavailable` promises to carry *the server's own* `detail`; folding a 500 or an undecodable body into it would mean inventing a message the server never sent. `unexpected` gets its own case. |
| 2026-08-25 | **No sidebar in v1** | `/ask` is the only route a chat UI can use — there is no endpoint listing papers. A `NavigationSplitView` with one item is ceremony; add it when a second screen exists. |
| 2026-08-25 | **Visual language from 5 Minute Journal** (before.click reference) | Warm cream ground, gold accent used once per screen, serif headlines with selective italic. Its *marketing* claims — "#1 app", review counts, press logos — are deliberately not reproduced; those are another company's credentials. |
| 2026-08-25 | Accent gold is **one element per screen** | Encoded as a comment in `Colors.swift`. Decorative accent is the main way this palette turns generic. |
| 2026-08-25 | **Impeccable adopted as the design-taste toolkit** — https://github.com/pbakaus/impeccable | Aksh's call. Skill + 23 commands + 59 deterministic detectors, aimed at the default AI house style. Pairs with the Vercel guidelines: Vercel answers "is this correct", Impeccable answers "is this any good". **Not installed** — `/impeccable init` failed because the command doesn't exist here; installing writes harness config and hooks, so it needs approval and a reload. Also note `init` offers to write `DESIGN.md`, which on case-insensitive macOS **is** the existing `Design.md`. |
| 2026-08-25 | **UI built against the Vercel Web Interface Guidelines** — https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md | Aksh's call. Covers a11y, focus, forms, animation, typography, perf, URL state, i18n, copy, plus an anti-pattern list, and doubles as a review command to run against UI code. **Referenced, not vendored** — stable canonical URL, and a local copy would drift. Caveat: these are *web* guidelines and do not transfer directly to native SwiftUI; see Design.md. |
| 2026-08-25 | Testability enforced by a guard running **opposite** to Phase 5's | Phase 5's failure mode is a hypothesis restating the gap, so `restates_gap` demands *new* terms. Phase 6's is a design not testing the hypothesis, so `measures_something_else` demands *overlapping* terms — separately on manipulated↔manipulation and measured↔measurement, so a design that varies the right thing but measures its latency is caught. Measured: on-target overlap floors at 3, off-target ceilings at 1; threshold 2 sits in the empty band. |
| 2026-08-25 | `ExperimentDesign` requires a **`discriminating_outcome`** | The result that comes out the other way. A design that can't produce the hypothesis's falsifier isn't a test of it. |
| 2026-08-25 | `/experiments` limit ceiling **5**, default 2 | Three Claude calls per gap (assess, propose, design) — the tightest spend guard in the codebase. 5×3=15 stays under `/hypotheses`' 10×2=20 ceiling. |
| 2026-08-25 | `Experiments` reports **two counts**, not one | Distinguishes "no gaps" from "gaps but no hypothesis survived" from "hypotheses but no design survived the guard" — three genuinely different outcomes. |
| 2026-08-17 | Falsifiability enforced by **output shape + a deterministic guard**, not by prompting | `Phases.md` demands hypotheses be specific and falsifiable, not restatements. `Hypothesis` requires an explicit `falsifier` field — a vague restatement cannot produce one — and `restates_gap()` rejects statements contributing under 3 substantive terms beyond the gap's own wording. The guard is key-free, so it is the one part of that criterion actually machine-checked. |
| 2026-08-17 | `hypothesize()` **finds gaps itself** rather than accepting a posted gap | A `CandidateGap` posted by a client couldn't be validated against the database — exactly the fabrication vector Phases 4–5 spent their validation code closing. Gaps must come out of `find_gaps`. |
| 2026-08-17 | `/hypotheses` reports **`gaps_considered`**, not a `found` boolean | Distinguishes "no gaps in the graph" from "gaps found but every proposal was refused". Same size, strictly more information. |
| 2026-08-17 | `/hypotheses` limit ceiling **10, not 50** | This path costs two Claude calls per gap (assess, then propose), so the spend guard is tighter than `/gaps`'. |
| 2026-08-17 | `GapWorker.run(session, limit)` — **one capability, no sibling method** | `GraphWorker` set the precedent of giving `run()` the real signature rather than the protocol's `question: str`. Unlike graph, gap detection has exactly one capability, so a second method would be an abstraction nobody asked for. |
| 2026-08-17 | `/gaps` `limit` bounded **1–50** | It governs how many Claude calls happen, so the bound is a spend guard, not input hygiene. |
| 2026-08-17 | Gap **prescore is `bridges + distinct papers`** (missing link) / `distinct papers` (contradiction) | Two lines of arithmetic counting independent support. Explicitly a heuristic ordering, not a truth claim — weights get fitted when there's data to fit them to. Its job is bounding Claude calls, not being right. |
| 2026-08-17 | **`limit` bounds Claude calls, not candidates gathered** | Spend follows the limit, not the graph. Verified: 15 candidates with `limit=3` makes exactly 3 calls. Default 10, since one call per candidate makes it a spend cap. |
| 2026-08-17 | **One `CandidateGap` type with a `kind` discriminator** (`missing_link` \| `contradiction`) | Part 4 returns a single JSON array over HTTP; two parallel types would force the route to merge them anyway. |
| 2026-08-17 | Significance scale **1–3, validated by membership not clamping** | An out-of-range value is a bad shape, so it's logged and skipped rather than silently coerced into something the model didn't say. |
| 2026-08-17 | Ranking output is **stably ordered** | Same graph and same verdicts must give the same order, or part 4's route returns a shuffling list and nothing downstream is testable. |
| 2026-08-13 | Extraction asks for **JSON, validated before persistence** | Same discipline as citations: the model proposes, our code validates. An edge referencing an undeclared concept is dropped and logged, never persisted as a dangling node. |
| 2026-08-13 | **Shared Anthropic client** moved to `app/agents/claude.py` | Extraction became the second consumer, so the seam earned its own module. A pure move — no retry/config/wrapper added. `MissingAPIKeyError` stays importable from `synthesis` so `main.py` is unbroken. |
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
- **The Ollama path has never talked to a real Ollama.** Every test uses `httpx.MockTransport`;
  Ollama is deliberately not installed on this machine. Request/response shape is checked
  against Ollama's `docs/api.md`, but *answer quality* from a 7B local model is unproven, and
  so is `num_ctx=8192` being enough for the 2000-token synthesis and graph prompts.
- `ollama.py` pins `num_ctx` to one constant for every call site. If a prompt outgrows it,
  Ollama truncates silently — the `ponytail:` note names the upgrade path (make it a setting).
- Ollama binds `127.0.0.1` by default. If the api container can't reach it via
  `host.docker.internal`, start it as `OLLAMA_HOST=0.0.0.0 ollama serve`.
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
- **The contradiction judge has never run against the live model** — same key gap. Candidate
  finding (`find_conflicting_claims`) is verified against the real dev DB with no key set;
  `judge_contradictions` is plumbing-tested only, so verdict *quality* is unproven.
- **Reverse-direction disagreements are not detected**: `A improves B` (p1) vs `B degrades A`
  (p2) yields no candidate, by the ordered-pair decision above. Known limitation, tested
  explicitly in `tests/test_contradictions.py`.
- **`POST /gaps` returns 503 live** and will until a key lands, so ranked-gap quality over
  HTTP is unproven end to end. The chain below it (SQL → worker → orchestrator → route) is
  proven with only Claude mocked.
- **`app/main.py` now has six routes**, against the 2026-08-10 decision to split into
  `app/api/` at three or more. Deliberately not done inside Phase 4's parts — it would have
  buried each part's diff. Overdue as its own change.
- **Phase 6 criterion 2 is unmeetable without a key** — "reviewed against at least one real
  hypothesis from Phase 5 for usefulness" needs output that has never been generated.
- **The testability guard has no stemming**, so `retrieval` ≠ `retriever` — an honest design can
  be rejected on wording. Overlap is also not aboutness: a design could echo the nouns without
  varying them. `ponytail:` comment names the upgrade (embedding distance, or an LLM judge
  asked whether the procedure could actually produce the falsifier).
- **Phase 5 criterion 3 is unmeetable without a key by definition** — "at least one real gap →
  hypothesis case reviewed for quality" requires output that has never been generated.
- **The restatement guard catches blatant restatements, not hedges.** Measured: restatements
  score 0–1 novel terms, real hypotheses 6–12, but "Chunk size has some effect on retrieval
  precision" scores exactly 3 and passes. No stemming, so a hedge reusing the gap's own
  improve/degrade wording also slips through. `ponytail:` comment names the replacement
  (embedding distance, or an LLM judge with a rubric) once there are labelled pairs to tune on.
- **Gap assessment has never run against the live model** — significance ratings and rationales
  are plumbing-tested only. Whether Claude can tell a real research gap from an obvious or
  extraction-artifact one is the entire value of part 3, and it is unproven.
- **First real `/ask`, 2026-08-26** (`qwen2.5:7b-instruct`, ~14s): the pipeline is correct —
  retrieval finds the right papers, citations map to real rows, indices resolve. Quality of the
  *prose* is mediocre: it writes in an annotated-bibliography voice ("[1] describes…") instead
  of synthesising, and one answer contradicted itself ("the papers do not directly discuss
  chunking" immediately after summarising two papers that do). A stronger model would likely fix
  the voice; the plumbing needs no change.
- **It also refuses honestly.** "How can retrieval hurt generation quality?" returned `found:
  true` with 5 chunks but **zero citations** and a plain statement that the passages don't
  answer it — rather than fabricating. Arguably over-cautious, but the right failure direction.
- **Extraction quality, measured 2026-08-25 against `qwen2.5:7b-instruct` (first real run):**
  Before the fixes — 3 papers → 44 concepts / 7 edges, **75% orphans**, 0 cross-paper links.
  After `MAX_CONCEPTS=8` + the orphan guard — 2 fresh papers → 13 concepts / 16 edges,
  **0 orphans**, edges now outnumber concepts.
- **Graph rebuilt 2026-08-26 after the `normalize()` fix** (wipe approved, papers untouched):
  27 papers → **104 concepts, 89 edges, 0 orphans, 3 papers failed**. Cross-paper linking went
  from **0 → 5 concepts spanning multiple papers**, two of them spanning 5 papers each. The
  earlier 44-concept/7-edge/75%-orphan shape is gone.
- **The remaining acronym limit is visible in the data**: `RAG` (2 papers) and
  `Retrieval-Augmented Generation` (5 papers) are still two nodes. Merged they would be the
  single biggest hub. Needs embedding-based entity resolution.
- **Embedding-based entity resolution measured and rejected, 2026-08-26.** All 5,356 concept
  pairs on the live graph, real fastembed: `RAG` ↔ `retrieval augmented generation` = **0.885**
  (51st percentile — the distance between two *random* concepts), against
  `hallucination`/`misinformation` 0.861 and `corpus poisoning`/`adversarial attack` 0.874.
  Same-concept spans [0.038, 0.885]; different-concept spans [0.051, 0.874] — **identical
  range, no threshold exists.** Cause is structural: MiniLM is a subword model, an acronym
  shares no subwords with its expansion, and it places `RAG` nearest `cloth rag` (0.144).
  Templating and evidence-concatenation rescues both failed — the second worse, collapsing
  `generation`↔`retrieval` to 0.025 by turning concept embeddings into paper embeddings.
  **Do not retry this approach.** No migration, no `Vector(384)` column on `concepts`.
- **Graph re-extracted 2026-08-26 with acronym resolution live** (wipe approved), on
  `qwen2.5:14b-instruct`: **30 papers, 0 failed, 158 concepts, 143 edges, 0 orphans**
  (previous run: 27 papers, 3 failed, 104 concepts, 89 edges).
- **⚠️ Correction — the acronym merge did NOT cause the improvement.** No `rag`/`llm`/`cot` row
  exists after this run, and `retrieval-augmented generation` spans 13 papers rather than 5.
  That was first attributed to the resolver. It is not. Tested directly: **the 14B never emits
  bare acronyms.** On a paper titled "…NLP Techniques and LLM-Based Retrieval…" it extracted
  `Natural Language Processing Techniques`, `Retrieval-Augmented Generation`, `Large Language
  Model` — every form expanded. Even "AR-RAG" became "Autoregressive Retrieval Augmentation".
  So the resolver almost certainly never fired; the concentration is the **model change** plus
  three more papers processed.
- **The resolver is still verified and still worth keeping.** `_acronym_row("rag")` returns the
  expansion's row id against the live graph and `_concept_id` creates no new row (tested in a
  rolled-back transaction; a control concept still inserts). The 7B *did* split `RAG` from its
  expansion, so this is insurance against an inconsistent extractor — it is simply not what
  improved this particular run. **Lesson: an absent duplicate row is not evidence a
  deduplicator ran.**
- **Corpus broadened 2026-08-26: 55 → 267 papers** across six adjacent areas (RAG, knowledge
  graph construction, representation learning, hallucination/factuality, agent memory, IR
  evaluation). Graph extracted over 53 of them: **325 concepts, 337 edges, 0 orphans**.
- **⚠️ The decisive finding: the hub penalty excludes 8 of the 11 cross-paper concepts.**
  Degree distribution is a power law — 176 concepts at degree 1, 85 at 2, 32 at 3, tailing to
  three hubs at 18/19/22. Mean degree 1.92, so the 2× cap sits at 3.83. Every concept that
  spans multiple papers sits *above* it:

  | concept | papers | degree | bridges? |
  |---|---|---|---|
  | large language models | 13 | 22 | excluded |
  | retrieval-augmented generation | 13 | 18 | excluded |
  | representation learning | 3 | 19 | excluded |
  | knowledge graph | 4 | 8 | excluded |
  | generative retrieval | 2 | 4 | excluded |
  | neural network | 2 | 2 | allowed |
  | state-of-the-art performance | 2 | 2 | allowed |
  | model protein | 2 | 2 | allowed |

  Cross-paper triads went 1 → 3, but two of the three are bridged by **junk** —
  `state-of-the-art performance` and `neural network`. The survivors are the concepts too
  trivial to have accumulated edges.
- **Diagnosis: raw degree is the wrong instrument.** It conflates two opposite things — a
  concept in 13 papers with degree 22 is genuinely central to the literature and is *exactly*
  what should bridge; a concept in 1 paper with degree 18 is one verbose abstract. The penalty
  treats them identically and removes both.
- **Fixed 2026-08-26 — the hub penalty now uses two guards, not raw degree.** Raw degree
  conflated two opposite failures and removed both. Replaced with:
  - **Ubiquity** — a bridge in more than `HUB_PAPER_FRACTION` (0.15) of papers-with-edges is
    dropped. Measured band: the two hubs sit at 24.5% of the corpus, the next bridge at 7.5%,
    nothing between. Floored at `MIN_UBIQUITOUS_PAPERS = 3`, because 15% of a four-paper corpus
    is 0.6 and a bare fraction would drop *every* bridge and return nothing — a real flaw the
    existing tests caught.
  - **Fan-out** — edges per paper the concept appears in, capped at `FAN_OUT_CAP = 3`. Catches
    the opposite failure that ubiquity cannot see: six concepts declared from one verbose
    abstract make fifteen triads while looking maximally specific.
  Neither substitutes for the other: `retrieval-augmented generation` is fan-out 1.4 but 25% of
  the corpus; a six-point single-paper star is 2% of the corpus but fan-out 6.0.
- **Result on the live graph: cross-paper triads 3 → 93.**
  **Correction:** an interim figure of 54 was reported from a mid-run snapshot. The extraction
  had not died — a `pgrep` on the wrong pattern (the process was `python -c`, with no matching
  filename) made a live run look dead, and a second extraction was started alongside it. Both
  completed; the duplication was wasted work but harmless, since extraction is idempotent.
  Final graph: **79 papers, 556 concepts, 629 edges**.
- **Both guards scale correctly at 79 papers** (ubiquity cap 11.85):

  | concept | papers | degree | fan-out | verdict |
  |---|---|---|---|---|
  | large language models | 19 | 25 | 1.3 | ubiquitous — dropped |
  | retrieval-augmented generation | 16 | 24 | 1.5 | ubiquitous — dropped |
  | hallucinations | 4 | 18 | 4.5 | verbose — dropped |
  | representation learning | 3 | 17 | 5.7 | verbose — dropped |
  | knowledge graph | 5 | 9 | 1.8 | **bridges** |
  | fine-tuning | 3 | 8 | 2.7 | **bridges** |

  Each guard is doing distinct work at this scale: two concepts dropped for ubiquity, two for
  fan-out, and neither would have been caught by the other.
- **⚠️ Remaining: near-duplicate gaps.** `knowledge graph` bridges 7 of the top 14, every one
  pairing `large language models` with a different neighbour of one paper. One bridge plus one
  endpoint still fans out into many near-identical gaps. Dedup by endpoint, or cap gaps per
  (bridge, endpoint) pair, is the next deterministic step.
- **⚠️ But cross-paper triads fell to 1**, and this is the method's real constraint, not a bug.
  Merging the acronym made `retrieval-augmented generation` a degree-14 hub in a 158-concept
  graph whose mean degree is ~1.8, so the hub penalty (2× mean ≈ 3.6) now correctly excludes it
  — along with every other concept of degree ≥4. **On a corpus that is ~50 RAG papers out of 55,
  "RAG" is a hub by construction, and open-triad discovery has almost nothing left to bridge.**
  Gap detection needs a *diverse* corpus; a single-topic one collapses into one hub plus leaves.
- **The one surviving gap is the best this project has produced**:
  `discrete molecular dynamics` ↔ `pruned-enriched Rosenbluth method`, bridged by
  `model protein`, from papers 11 and 13 — two different computational methods for simulating
  protein folding, both applied to model proteins, never directly compared. It came from the
  5-paper protein sub-corpus, not the 50-paper RAG one, which is exactly the point above.
- **Acronym identity solved structurally instead.** `is_acronym_of()` + `_acronym_row()` in
  `graph.py`: initials matching, one-directional, checked in `_concept_id` only after the exact
  match misses so the common path stays one query. On the live graph it fires on exactly 1 of
  5,356 pairs — `rag` == `retrieval augmented generation` — and declines `rag`/`ragpart`,
  `retrieval`/`retriever`, `rag`/`cloth rag`. Ceiling: an ambiguous acronym resolves to
  whichever expansion arrived first.
- **Hub penalty landed 2026-08-26.** `find_open_triads` drops bridges whose degree exceeds
  2× the graph mean, in SQL. Live: cross-paper triads 29 → 2, hub-bridged 82% → **0%**,
  top-10-for-assessment 9/10 hub-bridged → 0/10. Threshold is relative, not absolute, because
  edges grow linearly with papers while concepts grow sublinearly.
- **Controlled model experiment, 2026-08-26 — same graph, same prompts, same code, only the
  model swapped (`qwen2.5:7b-instruct` → `qwen2.5:14b-instruct`):**
  7B passed 3 of 4 candidates; 14B passed 2 of 4. The 14B rejected exactly one extra — the
  weakest, `RAG ↔ metrics`. Both passed the gaps bridged by generic words (`dataset`,
  `cognitive abilities`).
  **Doubling the model bought one rejection.** This is a design problem, not primarily a
  model-size problem.
- **The significance scale is dead.** Across both runs, every surviving gap was rated **2** on
  a 1–3 scale — the value 1 and the value 3 were never used once. So the assessor contributes
  no *ordering* information at all; it is a weak binary filter wearing a rating scale. The
  prescore is doing all the real ranking.
- **Next fix for judgement is deterministic, not a bigger model**: filter endpoint pairs by
  embedding distance using the fastembed model already in the project. A bridge like `dataset`
  or `metrics` connects endpoints that are semantically unrelated; a real gap connects two
  things close enough to plausibly belong together. That kills the generic-bridge case before
  a model is asked, the same way the hub penalty kills the topology case.
- **⚠️ The real problem now is hub artifacts, not fabrication.** Two concepts —
  `Retrieval-Augmented Generation` (degree 9) and `large language models (llms)` (degree 6) —
  bridge **82% of all 29 open triads**. A hub connected to N concepts manufactures ~N² "gaps"
  that are topology, not literature. Live output included
  `large language models <-> chat assistant systems`, which the assessor called "a significant
  opportunity for research" — one of the most explored connections in the field.
- **The LLM assessment layer did not catch it.** It rejected 2 of 5 candidates but passed the
  hub artifacts with confident rationales. So Substrate currently produces *plausible, well-cited,
  wrong* gaps: every paper is real, every edge is real, the judgement is not. This is the
  failure mode `Rules.md` names — and it arrives through judgement, not through fabrication,
  which is the one place none of the existing guards look.
- **Highest-leverage fix: penalise hub bridges deterministically.** Down-weight or exclude
  concepts above a degree threshold when forming triads, the way the prescore already works —
  a constraint rather than a prompt. Second: use the fastembed embeddings already present to
  drop endpoint pairs that are too semantically distant to be a real gap.
- **`POST /graph/build` can only ever process papers 1..N** — it does `ORDER BY id LIMIT n`, so
  there is no way to extract paper 4 without re-processing 1–3, and no way to resume. With 55
  papers this needs a cursor or an "unprocessed only" filter.
- 32 legacy orphan concepts remain from the pre-fix run; the fixed extraction creates none.
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

- **Hub concepts manufactured 93% of the cross-paper "gaps."** `find_open_triads` treated any
  shared neighbour as a bridge, so `Retrieval-Augmented Generation` (degree 7) and
  `large language models (llms)` (degree 4) — against a mean degree of 1.6 — produced 27 of the
  29 triads at `min_papers=2`, 9 of the 10 candidates sent for assessment, and the model rated
  `large language models <-> chat assistant systems` "a significant opportunity for research".
  Fixed in the SQL, not in Python: `gaps.py` now joins a degree CTE and bars any bridge above
  `HUB_DEGREE_FACTOR (2) x the graph's mean degree`. Relative, not a constant, so a bigger corpus
  raises its own cap. After: 2 triads at `min_papers=2`, 0% hub-bridged, 0/10 hub-bridged in the
  assessed top ten. The cap drops the *leg*, not the pair — a gap an ordinary concept also
  bridges survives, and a hub can still be a gap's endpoint.
  `tests/test_gap_ranking.py`'s `star` fixture was a literal hub, so it became `ring` (a cycle,
  every degree 2) — same cost property, a graph the guard doesn't legitimately empty.

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

# Run against a local model instead of the API (needs Ollama on the host):
#   brew install ollama && ollama serve
#   ollama pull qwen2.5:7b-instruct
LLM_PROVIDER=ollama docker compose up -d api
curl -X POST localhost:8000/graph/build -H 'Content-Type: application/json' \
  -d '{"limit":10}'
curl -X POST localhost:8000/graph/traverse -H 'Content-Type: application/json' \
  -d '{"concept":"<concept>","depth":2}'
```
