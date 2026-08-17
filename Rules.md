# Substrate — Rules

## Libraries
- Prefer: no strong preference stated — Claude Code decides what fits FastAPI + pgvector best (e.g. SQLAlchemy/Alembic if a migration story is needed, Pydantic for validation since it's FastAPI's default).
- Avoid: nothing explicitly ruled out. Open to anything that fits the stack.

## Error handling
Degrade gracefully — log and continue where possible, rather than hard-failing on every error. This applies especially to ingestion (a single bad paper/API timeout shouldn't kill a batch) and retrieval (a missing embedding shouldn't 500 the whole request). Reserve hard failures for cases where continuing would produce silently wrong output (e.g. malformed config, missing required API keys at startup).

## Testing
TDD. Write the test for a unit of work before implementing it, for every phase — not just MVP.

## Requires explicit approval before doing
- Deleting data or dropping tables
- Installing new dependencies
- Touching prod/deploy config
- **Pushing to GitHub** — commits are fine locally, but do not push until Aksh explicitly says so
- **Driving a browser / any browser automation** — do not use browser tools until Aksh explicitly says so

## Execution discipline (Karpathy guidelines — always included)
Use the `karpathy-guidelines` skill for this. Summary, in effect for every phase:
1. **Think before coding** — state assumptions explicitly; if multiple interpretations exist, present them, don't silently pick; stop and ask if genuinely unclear.
2. **Simplicity first** — minimum code that solves the task; no speculative abstraction, no unrequested configurability, no error handling for impossible cases.
3. **Surgical changes** — touch only what the task requires; don't refactor or "improve" adjacent code; match existing style; remove only orphans your own change created.
4. **Goal-driven execution** — every task starts with a stated, checkable success criterion; work in verify-then-proceed loops (implement → check against criterion → fix → re-check) rather than declaring done by feel.

## Looping mandate
Work phase-by-phase per Phases.md. Do not attempt multiple phases in a single pass. Each phase's done-criteria must be verifiably met — and tests must be passing — before starting the next.

## Memory.md maintenance
Create Memory.md after Phase 1 lands (template in the kickoff kit). From then on, keep it current continuously — not just at phase boundaries: update it whenever a file is added, removed, or a decision changes, not only when a phase completes. Memory.md should always reflect the true current state of the project.

## Sustainable coding plugin
Aksh wants the **"ponytail"** plugin used for sustainable coding practices. It's a Claude Code-side plugin (not part of this planning kit's catalog) — confirm it is installed/enabled in the Claude Code environment before Phase 1 starts, and apply it throughout.

## Development process — use subagents
Build Substrate using Claude Code subagents rather than a single monolithic session. Delegate distinct units of work (e.g. ingestion pipeline, retrieval, synthesis, later graph/gap/hypothesis logic) to separate subagents per phase, each scoped to its own goal and done-criteria from Phases.md. Coordinate results back through the main session; Memory.md stays the shared source of truth across subagents.
