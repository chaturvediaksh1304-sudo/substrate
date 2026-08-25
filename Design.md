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

## Still to decide at Phase 7

Visual tone, color palette, typography, light/dark mode, and any brand carryover from
MANK Studios or XSkill — the original interview questions, unanswered.
