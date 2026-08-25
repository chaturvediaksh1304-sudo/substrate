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

## Design language source: awesome-design-md

**awesome-design-md** — https://github.com/VoltAgent/awesome-design-md

Adopted 2026-08-17. 73 ready-made `DESIGN.md` files reverse-engineered from real websites,
grouped by category (AI/LLM platforms, developer tools, backend/DevOps, SaaS, fintech, and
more). Each follows Google Stitch's `DESIGN.md` spec: visual theme, color palette with hex and
semantic roles, a full type hierarchy, component styling with states, spacing and grid, an
elevation system, do's and don'ts, responsive behaviour, and an agent prompt guide. Each ships
with `preview.html` and `preview-dark.html` catalogs.

Used by picking a site whose design language suits Substrate and seeding our `DESIGN.md` from
it — a starting point, not a final identity. **Consequence worth naming:** adopt one wholesale
and Substrate looks like that company. It's a floor to build from, not the brand.

**Unlike the other two, this one largely survives a native port.** Color, type scale, spacing,
elevation and do's/don'ts are platform-agnostic design decisions that translate to SwiftUI
fine. Only the responsive-behaviour and CSS-component sections are web-bound. If the Mac app
goes native, this is the standard that still earns its keep.

## One file, three writers — resolve before running anything

`DESIGN.md` is now contested:

1. **This file.** macOS filesystems are case-insensitive, so `Design.md` and `DESIGN.md` are
   the same file.
2. **Impeccable's `/impeccable init`** writes `DESIGN.md`.
3. **awesome-design-md** says copy a `DESIGN.md` into the project root.

All three want the same thing in the same place, so the intent doesn't conflict — the risk is
purely **overwrite**. Order that works: seed from an awesome-design-md pick, refine with
Impeccable, keep the standards sections above intact at the top. Back this file up before
running `init` or pasting anything over it.

## Still to decide at Phase 7

Visual tone, color palette, typography, light/dark mode, and any brand carryover from
MANK Studios or XSkill — the original interview questions, unanswered.
