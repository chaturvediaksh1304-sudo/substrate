import logging
import re
from dataclasses import dataclass

import anthropic

from app.agents.claude import MissingAPIKeyError, _client  # noqa: F401 - re-exported: app.main imports it from here
from app.agents.retrieval import RetrievedChunk
from app.config import settings

log = logging.getLogger(__name__)

# Enough for a few paragraphs and a citation trail; the answer is a synthesis, not a paper.
MAX_TOKENS = 2000

SYSTEM = (
    "You synthesize answers to research questions from passages of academic papers.\n"
    "Ground every claim in the numbered passages you are given, and mark it with that "
    "source's bracketed index, e.g. [1] or [2][3].\n"
    "Cite only those indices. Never invent a source, an index, a title or an author.\n"
    "If the passages do not answer the question, say so plainly instead of filling the "
    "gap from your own knowledge."
)

CITATION_PATTERN = re.compile(r"\[(\d+)\]")


@dataclass
class Citation:
    """A real paper the answer cited, built from a RetrievedChunk — never from the model's prose."""

    index: int
    title: str
    authors: list[str]
    year: int | None
    url: str | None
    external_id: str


@dataclass
class SynthesisResult:
    answer: str
    citations: list[Citation]


def _sources(chunks: list[RetrievedChunk]) -> list[tuple[RetrievedChunk, list[str]]]:
    """One numbered source per paper, in retrieval order, with its passages grouped under it."""
    grouped: dict[int, tuple[RetrievedChunk, list[str]]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk.paper_id, (chunk, []))[1].append(chunk.text)
    return list(grouped.values())


def _prompt(question: str, sources: list[tuple[RetrievedChunk, list[str]]]) -> str:
    blocks = [
        "[{i}] {title} ({authors}, {year})\n{passages}".format(
            i=i,
            title=paper.title,
            authors=", ".join(paper.authors) or "unknown authors",
            year=paper.year or "n.d.",
            passages="\n".join(passages),
        )
        for i, (paper, passages) in enumerate(sources, start=1)
    ]
    return f"Question: {question}\n\nSources:\n\n" + "\n\n".join(blocks)


def _cited(answer: str, sources: list[tuple[RetrievedChunk, list[str]]]) -> list[Citation]:
    """Map the [n] the model actually used back to real papers; drop any n we can't back."""
    citations = []
    for index in sorted({int(n) for n in CITATION_PATTERN.findall(answer)}):
        if not 1 <= index <= len(sources):
            log.warning("synthesis: dropping out-of-range citation [%d]", index)
            continue
        paper = sources[index - 1][0]
        citations.append(
            Citation(
                index=index,
                title=paper.title,
                authors=paper.authors,
                year=paper.year,
                url=paper.url,
                external_id=paper.external_id,
            )
        )
    return citations


def synthesize(question: str, chunks: list[RetrievedChunk]) -> SynthesisResult:
    """Ask Claude to answer from the chunks. Raises rather than inventing an answer."""
    sources = _sources(chunks)
    try:
        message = _client().messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user", "content": _prompt(question, sources)}],
        )
    except anthropic.APIError as exc:
        log.warning("synthesis: Anthropic API call failed: %s", exc)
        raise
    answer = "".join(block.text for block in message.content if block.type == "text")
    return SynthesisResult(answer=answer, citations=_cited(answer, sources))
