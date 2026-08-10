# Substrate — PRD

## One-liner
An AI research assistant that takes a complex question, searches and synthesizes across research papers, connects related concepts, surfaces gaps in the literature, forms hypotheses, and eventually helps design experiments to test them.

## Problem
Doing real research today is bottlenecked in four ways at once:
- Manually reading dozens of papers to build a full picture is too slow.
- Existing tools (ChatGPT, Elicit, etc.) can summarize individual papers but don't connect concepts *across* papers into a coherent map.
- No existing tool goes end-to-end from literature review to an actual proposed experiment.
- Researchers routinely miss gaps and contradictions buried across the literature simply because no one can hold that much in their head at once.

There is no unified tool that reasons across the full research loop — search, synthesize, connect, find gaps, hypothesize, design experiments.

## Target user
Open tool for anyone doing research — not locked to academia or a single company. Early usage will be Aksh himself as the first user/tester.

## MVP scope (must-have)
- Ingest papers from Semantic Scholar / arXiv for a given topic/question
- Chunk and embed paper content, store in a retrievable index
- Accept a research question via API and retrieve relevant passages
- Synthesize an answer from retrieved passages using an LLM, with citations back to source papers
- This is Phase 1–2 in Phases.md — RAG + Q&A working end-to-end, nothing more

## Explicitly out of scope (for now)
- Knowledge graph, gap detection, hypothesis generation, experiment design — these are real and planned, but as later phases (Phase 3–6), not MVP
- Web UI — planned for Phase 7, after the API is stable
- Auth / multi-user accounts — planned for Phase 8, OAuth (Google/GitHub)
- Mobile app — last in sequence (Phase 9), only after the web app is proven

**Sequencing note:** nothing above is permanently cut — it's ordered. Backend/API first, then web UI, then mobile, in that order, once each prior layer is stable.

## Success looks like
MVP is working when Substrate surfaces a gap or connection in the literature that Aksh didn't already know about, from a real research question he feeds it.
