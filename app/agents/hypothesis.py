import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.claude import MissingAPIKeyError, _client
from app.agents.gaps import CandidateGap, GapPaper
from app.agents.graph import RelatedEdge, traverse
from app.config import settings
from app.models import Paper

log = logging.getLogger(__name__)

# Five short fields. Room for a sentence or two each, and no room for an essay.
MAX_TOKENS = 800

# One hop, not graph.DEFAULT_DEPTH's two: the claims that touch the gap's own endpoints are
# the ones a hypothesis has to answer to, and two hops floods the prompt with a neighbour's
# neighbours. ponytail: raise it if generated hypotheses turn out to ignore obvious context.
NEIGHBOURHOOD_DEPTH = 1

SYSTEM = (
    "You turn a research gap, found in a knowledge graph built from paper abstracts, into "
    "one testable hypothesis.\n"
    "Reply with JSON only, in exactly this shape:\n"
    '{"statement": "the hypothesis, one sentence", '
    '"manipulation": "what would be varied or compared", '
    '"measurement": "what would be measured, and on what", '
    '"predicted_effect": "which way it goes, and roughly how much", '
    '"falsifier": "the observation that would show this hypothesis is wrong", '
    '"paper_ids": [paper id, ...]}\n'
    "The statement must be specific and falsifiable. It must not restate the gap: "
    '"there may be an unexplored relationship between X and Y" is a restatement and will be '
    'rejected; "X reduces Y on in-domain inputs but increases it on out-of-domain inputs" is '
    "a hypothesis.\n"
    "The falsifier is a concrete observation, not a hedge. If you cannot name one, you have "
    "not written a hypothesis.\n"
    "Do not design an experiment: no protocol, no sample size, no apparatus, no procedure.\n"
    '"paper_ids" lists the papers shown to you that this hypothesis rests on, and only ids '
    "shown to you. Never invent a paper, a title, an author or a finding.\n"
    "No prose, no markdown fences, no explanation outside the JSON."
)

REQUIRED = ("statement", "manipulation", "measurement", "predicted_effect", "falsifier")


@dataclass
class Hypothesis:
    """A testable claim generated from one gap, with the rows that grounded it.

    The shape is the point: a vague restatement of the gap cannot fill `manipulation`,
    `measurement`, `predicted_effect` and — above all — `falsifier`, so demanding all five
    does more for criterion 2 than asking the model nicely for specificity.

    `papers` are the gap's own paper rows, filtered to the ones the model said it leaned on.
    They come from the database, never from the model's prose: a hypothesis grounded in a
    paper that does not exist is exactly the silently-wrong output `Rules.md` hard-fails on.
    """

    gap: CandidateGap
    statement: str
    manipulation: str  # the independent side: what would be varied or compared
    measurement: str  # the dependent side: what would be measured, and on what
    predicted_effect: str  # direction, and rough size where the model can name one
    falsifier: str  # what would have to be observed for this to be wrong
    papers: list[GapPaper]


# Ordinary English glue, plus the vocabulary a gap restatement is actually made of:
# hedges, "relationship / link / connection", and the "further research is needed" register.
# Stripping both leaves only the terms a statement contributes on its own.
_STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those of in on at to for from with
    without by as is are was were be been being it its their there here we our us you your
    they them he she his her not no nor so such very more most much many some any all both
    each other others same own between among across over under above below within into onto
    about after before during while when where which who whom whose what how why yet still
    do does did done can could may might must shall should will would has have had
    per via versus vs also thus hence therefore
    gap gaps unexplored underexplored understudied unstudied unclear unknown missing
    relationship relationships relation relations relate relates related link links linked
    connect connects connected connection connections associate associated association
    intersection interaction interplay
    potential potentially possible possibly perhaps likely maybe seem seems appear appears
    suggest suggests suggested indicate indicates
    research researcher researchers study studies studied studying investigate investigates
    investigated investigation examine examines examined explore explores explored
    exploration understand understanding
    further future work needed needs need warrant warrants warranted merit merits deserve
    deserves interesting important significant novel promising area areas topic topics
    direction directions avenue avenues question questions open literature paper papers
    corpus field domain-level somebody someone nobody
    """.split()
)

# ponytail: a crude guard — set difference on tokens, no stemming, no synonyms. Measured on
# 20 example pairs it separates cleanly (restatements score 0-1 novel terms, real hypotheses
# 6-12), but both edges are real and known:
#   - false negative, no stemming: "Distillation may improve or degrade accuracy depending on
#     context." scores 4 against a gap whose rationale says "improves or degrades", because
#     improve != improves. A hedge gets through.
#   - false positive, count is not quality: "Retrieval augmentation causally reduces
#     hallucination." scores 2 and is rejected, though it is arguably directional enough.
# It exists because it is deterministic and testable with no API key. Replace it with an
# embedding-distance check against the gap text, or an LLM judge with a rubric, once there
# are labelled vague/specific pairs to tune a threshold against.
MIN_NOVEL_TERMS = 3

_TOKEN = re.compile(r"[a-z0-9]+(?:[-.'][a-z0-9]+)*")


def _terms(text: str) -> set[str]:
    return {token for token in _TOKEN.findall(text.lower()) if token not in _STOPWORDS}


def novel_terms(statement: str, gap_text: str) -> set[str]:
    """What the statement says that the gap did not. Exposed so a caller can see the margin."""
    return _terms(statement) - _terms(gap_text)


def restates_gap(statement: str, gap_text: str, min_novel: int = MIN_NOVEL_TERMS) -> bool:
    """True when the statement adds nothing to the gap's own words. Pure strings, no model.

    `gap_text` is the gap's concept names plus its rationale — the wording a restatement
    would be built from.
    """
    return len(novel_terms(statement, gap_text)) < min_novel


def _gap_text(gap: CandidateGap) -> str:
    return f"{gap.concept_a} {gap.concept_b} {gap.rationale}"


def generate_hypothesis(session: Session, gap: CandidateGap) -> Hypothesis | None:
    """One gap plus its real paper and graph context in, one testable hypothesis out.

    Per `Rules.md`, an API error, unparseable output, a missing field or a statement that
    merely restates the gap is logged and returns `None` — a fabricated hypothesis presented
    as grounded is worse than no hypothesis. A missing key is config, not data, and fails loud.
    """
    abstracts = _abstracts(session, gap)
    edges = _neighbourhood(session, gap)
    try:
        hypothesis = _propose(gap, abstracts, edges)
    except MissingAPIKeyError:
        raise
    except Exception as exc:
        log.warning(
            "hypothesis: no hypothesis for %s %s / %s: %s",
            gap.kind,
            gap.concept_a,
            gap.concept_b,
            exc,
        )
        return None
    log.info(
        "hypothesis: %s %s / %s -> %r",
        gap.kind,
        gap.concept_a,
        gap.concept_b,
        hypothesis.statement,
    )
    return hypothesis


def _abstracts(session: Session, gap: CandidateGap) -> list[tuple[GapPaper, str]]:
    """The gap's papers with their abstracts, in the gap's own order. One query, not N."""
    by_id = dict(
        session.execute(
            select(Paper.id, Paper.abstract).where(Paper.id.in_([p.id for p in gap.papers]))
        ).all()
    )
    return [(paper, by_id.get(paper.id, "")) for paper in gap.papers]


def _neighbourhood(session: Session, gap: CandidateGap) -> list[RelatedEdge]:
    """What the graph already claims around both endpoints. Reuses graph.traverse, one per end."""
    seen: set[tuple[str, str, str, int]] = set()
    edges = []
    for concept in (gap.concept_a, gap.concept_b):
        for edge in traverse(session, concept, NEIGHBOURHOOD_DEPTH).edges:
            key = (edge.source, edge.relation, edge.target, edge.paper_id)
            if key not in seen:
                seen.add(key)
                edges.append(edge)
    return edges


def _propose(
    gap: CandidateGap, abstracts: list[tuple[GapPaper, str]], edges: list[RelatedEdge]
) -> Hypothesis:
    try:
        message = _client().messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user", "content": _prompt(gap, abstracts, edges)}],
        )
    except anthropic.APIError as exc:
        raise RuntimeError(f"Anthropic API call failed: {exc}") from exc
    raw = "".join(block.text for block in message.content if block.type == "text")
    return _hypothesis(raw, gap)


def _prompt(
    gap: CandidateGap, abstracts: list[tuple[GapPaper, str]], edges: list[RelatedEdge]
) -> str:
    """Only rows: the gap as detected, the papers' own abstracts, the graph's own claims."""
    if gap.kind == "missing_link":
        header = (
            "No paper in this corpus links these two concepts directly:\n"
            f"- {gap.concept_a}\n- {gap.concept_b}\n"
            f"Both are linked to: {', '.join(gap.evidence)}"
        )
    else:
        header = (
            f"Papers in this corpus disagree about {gap.concept_a} -> {gap.concept_b}. "
            "What they say:\n" + "\n".join(f"- {sentence}" for sentence in gap.evidence)
        )
    papers = "\n".join(
        f"- paper {paper.id}: {paper.title}\n  {abstract}" for paper, abstract in abstracts
    )
    claims = (
        "\n".join(
            f'- {edge.source} {edge.relation} {edge.target} — "{edge.evidence}" '
            f"[paper {edge.paper_id}: {edge.title}]"
            for edge in edges
        )
        or "- (nothing else in the graph touches either concept)"
    )
    return (
        f"{header}\n\n"
        f"Why this was flagged as a gap: {gap.rationale}\n\n"
        f"Papers behind the gap:\n{papers}\n\n"
        f"What the graph already claims around these concepts:\n{claims}"
    )


def _hypothesis(raw: str, gap: CandidateGap) -> Hypothesis:
    """The model proposes; this decides. Raises on anything the caller must not return."""
    # Models wrap JSON in ``` fences out of habit, prompt or no prompt.
    payload = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))

    fields = {}
    for name in REQUIRED:
        value = str(payload.get(name) or "").strip()
        if not value:
            raise ValueError(f"{name} is missing or empty")
        fields[name] = value

    if restates_gap(fields["statement"], _gap_text(gap)):
        raise ValueError(f"statement restates the gap, adds nothing: {fields['statement']!r}")

    known = {paper.id for paper in gap.papers}
    named = [paper_id for paper_id in (payload.get("paper_ids") or []) if paper_id in known]
    if unknown := [pid for pid in (payload.get("paper_ids") or []) if pid not in known]:
        log.warning("hypothesis: dropping paper id(s) outside the gap: %s", unknown)
    # Naming none of them (or only fabricated ones) is not grounds to drop the hypothesis —
    # it just means the model did not narrow, so the gap's own papers stand as the grounding.
    papers = [paper for paper in gap.papers if paper.id in named] or gap.papers
    return Hypothesis(gap=gap, papers=papers, **fields)


# Two Claude calls per gap, not one — the gap is assessed before it is hypothesized over —
# so this cap bites twice as hard as /gaps' does, and a researcher reads hypotheses one at
# a time anyway. ponytail: raise it when someone actually wants a batch of them.
HYPOTHESIS_LIMIT = 3


class HypothesisWorker:
    """Turns one gap into one testable hypothesis. The orchestrator registers by `name`."""

    name = "hypothesis"

    # Hypothesis generation reasons over a gap, not a question, so `run` takes the session
    # and the gap — the same latitude GapWorker's `run(session, limit)` takes. The Worker
    # protocol is structural and asks only for `name` + `run`; it stays satisfied.
    # `None` back means the proposal was refused (restatement, bad shape, API error) —
    # generate_hypothesis' own contract, passed through rather than reinterpreted here.
    def run(self, session: Session, gap: CandidateGap, **kwargs: Any) -> Hypothesis | None:
        return generate_hypothesis(session, gap)
