# Substrate — Design

No UI yet. Substrate is backend/API-only through Phase 6, which is now built — so this file
becomes live at Phase 7, whatever form that client takes (a macOS app was raised on
2026-08-17 and is undecided; see Memory.md).

## Standard we build UI against

**Vercel Web Interface Guidelines** — https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md

Adopted 2026-08-17. Not vendored here on purpose: it lives at a stable canonical URL, and a
copy in this repo would silently drift from the source. Fetch it when doing UI work.

It covers accessibility, focus states, forms, animation, typography, content handling, images,
performance, navigation and URL state, touch, safe areas, dark mode, i18n, hydration, hover
states, and copy — plus an explicit anti-pattern list. It is also written as a *review
command*, so it can be run against UI code as a compliance pass, not just read as guidance.

**Scope caveat that matters for the client decision:** these are **web** guidelines. They are
written in HTML/CSS/React terms — `aria-label`, `:focus-visible`, `Intl.*`, hydration safety,
`<Link>`, `prefers-reduced-motion`. They apply in full to a web UI or a web-based shell
(Tauri/Electron). They do **not** transfer directly to native SwiftUI, which has its own
equivalents (`accessibilityLabel`, Dynamic Type, `.accessibilityReduceMotion`, `Intl` →
`FormatStyle`). Adopting this standard is therefore an argument in favour of a web-based
client — or it means writing the SwiftUI equivalents by hand.

## Design tooling: Impeccable

**Impeccable** — https://github.com/pbakaus/impeccable · https://impeccable.style

Adopted 2026-08-17. A design-guidance toolkit for AI coding agents: one skill, 23 commands
(`init`, `shape`, `craft`, `critique`, `audit`, `polish`, `harden`, `animate`, `typeset`,
`layout`, `distill`, and more), and 59 deterministic detector rules that run without an LLM
or API key. It exists to defeat the house style every model defaults to — Inter everywhere,
purple-to-blue gradients, cards inside cards, an icon tile above every heading.

**Not installed yet.** `/impeccable` is not available in this environment, which is why
`/impeccable init` failed. Installing writes into harness folders (`.claude/` or `~/.claude`)
and adds a hook manifest, so it needs Aksh's explicit approval per `Rules.md`, and the harness
must be reloaded before the command appears.

**Watch on install:** `/impeccable init` writes `PRODUCT.md` and offers to write `DESIGN.md`.
macOS filesystems are case-insensitive, so `DESIGN.md` **is** this file — an unattended `init`
can overwrite everything here. Back this file up before running it.

### How the two standards divide

| | Vercel Web Interface Guidelines | Impeccable |
|---|---|---|
| Answers | Is this correct? | Is this any good? |
| Covers | a11y, focus, forms, perf, URL state, i18n, hydration | taste, hierarchy, type, color, motion, emotional resonance |
| Enforcement | review command → `file:line` findings | 59 deterministic detectors + LLM critique |

They complement rather than overlap. Both are **web-frontend** tools, so the SwiftUI caveat
above applies to Impeccable equally.

## Still to decide at Phase 7

Visual tone, color palette, typography, light/dark mode, and any brand carryover from
MANK Studios or XSkill — the original interview questions, unanswered.
