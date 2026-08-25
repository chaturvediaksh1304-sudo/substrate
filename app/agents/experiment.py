import json
import logging
from dataclasses import dataclass
from typing import Any

import anthropic
from sqlalchemy.orm import Session

from app.agents.claude import MissingAPIKeyError, _client
from app.agents.gaps import GapPaper
# `_terms` is hypothesis.py's tokenizer — the half of `novel_terms` that is reusable here.
# `novel_terms` itself is a set *difference* and this guard needs the intersection;
# `_abstracts` only ever touches `.papers`, which a Hypothesis carries exactly as a gap does.
from app.agents.hypothesis import Hypothesis, _abstracts, _terms
from app.config import settings

log = logging.getLogger(__name__)

# A procedure plus five short fields. Room for a paragraph of method and a sentence or two
# each for the rest, and no room for a paper.
MAX_TOKENS = 1200

SYSTEM = (
    "You turn one testable hypothesis into one experiment that would test it.\n"
    "Reply with JSON only, in exactly this shape:\n"
    '{"method": "the procedure, one paragraph", '
    '"manipulated": "the variable that is varied, and the levels it takes", '
    '"measured": "the variable that is measured, and how", '
    '"controlled": ["what is held constant", ...], '
    '"expected_outcome": "what is observed if the hypothesis holds", '
    '"discriminating_outcome": "the result that would come out the other way and show the '
    'hypothesis is wrong", '
    '"paper_ids": [paper id, ...]}\n'
    "The design must test THIS hypothesis. Manipulate what the hypothesis says is varied and "
    "measure what the hypothesis says is measured — not something adjacent to them.\n"
    "The procedure must be able to produce the hypothesis's own falsifier. If it cannot, it "
    "is a demonstration, not a test, and will be rejected.\n"
    '"discriminating_outcome" is a concrete result, not a hedge, and it must be the opposite '
    'branch of "expected_outcome" — the two cannot both happen.\n'
    "Do not do statistics: no power analysis, no sample-size calculation, no choice of test, "
    "no ethics or approval sections.\n"
    '"paper_ids" lists the papers shown to you that this design builds on, and only ids '
    "shown to you. Never invent a paper, a title, an author or a finding.\n"
    "No prose, no markdown fences, no explanation outside the JSON."
)

REQUIRED = ("method", "manipulated", "measured", "expected_outcome", "discriminating_outcome")


@dataclass
class ExperimentDesign:
    """One experiment that would test one hypothesis, with the rows that grounded it.

    The shape is the point, exactly as it is for `Hypothesis`. Phase 5's failure mode was a
    hypothesis restating its gap; Phase 6's is a design that sounds like methodology but does
    not test the hypothesis. Adjacent prose can fill `method` — it cannot fill
    `discriminating_outcome`, because naming the result that comes out the other way forces
    the design to be capable of producing the hypothesis's own `falsifier`, and it cannot fill
    `manipulated`/`measured` with the hypothesis's own variables (see `measures_something_else`).

    `controlled` is what is held constant. A design that holds nothing constant has not
    separated its manipulation from everything else it changed.

    `papers` are the hypothesis's own paper rows, filtered to the ones the model said it
    leaned on. They come from the database, never from the model's prose.
    """

    hypothesis: Hypothesis
    method: str  # the procedure, start to finish
    manipulated: str  # the independent variable, and the levels it takes
    measured: str  # the dependent variable, and how it is read off
    controlled: list[str]  # what is held constant so the manipulation is the only difference
    expected_outcome: str  # what is observed if the hypothesis holds
    discriminating_outcome: str  # the result that would come out the other way instead
    papers: list[GapPaper]


# ponytail: a crude guard — set intersection on tokens, no stemming, no synonyms, and it
# runs the opposite way to hypothesis.restates_gap (which demands the statement introduce
# terms *beyond* the gap; this demands the design reuse the hypothesis's *own* terms).
# Measured on nine example pairs with the same tokenizer, as (manipulated / measured) shared
# terms: on-target designs score 3/6, 3/5 and 8/5; off-target ones 0/0, 0/1, 0/0 and 3/0
# (that last is the real failure mode — right manipulation, wrong measurement). The
# threshold sits in the empty band between 1 and 3. Both edges are real and known:
#   - false negative, no stemming: a design measuring "retrieval quality" against a
#     hypothesis measuring "the retriever's precision" shares nothing, because
#     retrieval != retriever. An honest design gets rejected.
#   - false positive, overlap is not aboutness: a design can score by echoing the
#     hypothesis's nouns in a method that never actually varies them.
# It exists because it is deterministic and testable with no API key. Replace it with an
# embedding-distance check between the variable pairs, or an LLM judge asked whether the
# procedure could produce the hypothesis's falsifier, once there are labelled
# tests-it/doesn't-test-it pairs to tune a threshold against.
MIN_SHARED_TERMS = 2


def shared_terms(design_text: str, hypothesis_text: str) -> set[str]:
    """What the design and the hypothesis both say. Exposed so a caller can see the margin."""
    return _terms(design_text) & _terms(hypothesis_text)


def measures_something_else(
    manipulated: str,
    measured: str,
    manipulation: str,
    measurement: str,
    min_shared: int = MIN_SHARED_TERMS,
) -> bool:
    """True when the design's variables are not the hypothesis's. Pure strings, no model.

    Both sides must overlap: a design that varies the right thing and then measures its
    latency is the exact failure this phase is built against.
    """
    return (
        len(shared_terms(manipulated, manipulation)) < min_shared
        or len(shared_terms(measured, measurement)) < min_shared
    )


def design_experiment(session: Session, hypothesis: Hypothesis) -> ExperimentDesign | None:
    """One hypothesis plus its real paper context in, one experiment that tests it out.

    Per `Rules.md`, an API error, unparseable output, a missing field or a design whose
    variables are not the hypothesis's is logged and returns `None` — a plausible-sounding
    protocol that tests nothing is worse than no design. A missing key is config, not data,
    and fails loud.
    """
    abstracts = _abstracts(session, hypothesis)
    try:
        design = _propose(hypothesis, abstracts)
    except MissingAPIKeyError:
        raise
    except Exception as exc:
        log.warning("experiment: no design for %r: %s", hypothesis.statement, exc)
        return None
    log.info("experiment: %r -> %r", hypothesis.statement, design.method)
    return design


def _propose(
    hypothesis: Hypothesis, abstracts: list[tuple[GapPaper, str]]
) -> ExperimentDesign:
    try:
        message = _client().messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user", "content": _prompt(hypothesis, abstracts)}],
        )
    except anthropic.APIError as exc:
        raise RuntimeError(f"Anthropic API call failed: {exc}") from exc
    raw = "".join(block.text for block in message.content if block.type == "text")
    return _design(raw, hypothesis)


def _prompt(hypothesis: Hypothesis, abstracts: list[tuple[GapPaper, str]]) -> str:
    """Only rows: the hypothesis as generated, and the papers' own abstracts behind it."""
    papers = "\n".join(
        f"- paper {paper.id}: {paper.title}\n  {abstract}" for paper, abstract in abstracts
    )
    return (
        f"Hypothesis: {hypothesis.statement}\n\n"
        f"What it says is varied or compared: {hypothesis.manipulation}\n"
        f"What it says is measured: {hypothesis.measurement}\n"
        f"The effect it predicts: {hypothesis.predicted_effect}\n"
        f"What would show it is wrong: {hypothesis.falsifier}\n\n"
        f"The gap it came from: {hypothesis.gap.rationale}\n\n"
        f"Papers behind it:\n{papers}\n\n"
        "Design one experiment that could produce either the predicted effect or the "
        "falsifier."
    )


def _design(raw: str, hypothesis: Hypothesis) -> ExperimentDesign:
    """The model proposes; this decides. Raises on anything the caller must not return."""
    # Models wrap JSON in ``` fences out of habit, prompt or no prompt.
    payload = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))

    fields = {}
    for name in REQUIRED:
        value = str(payload.get(name) or "").strip()
        if not value:
            raise ValueError(f"{name} is missing or empty")
        fields[name] = value

    controlled = [str(item).strip() for item in (payload.get("controlled") or [])]
    controlled = [item for item in controlled if item]
    if not controlled:
        raise ValueError("controlled is missing or empty: the design holds nothing constant")

    if measures_something_else(
        fields["manipulated"],
        fields["measured"],
        hypothesis.manipulation,
        hypothesis.measurement,
    ):
        raise ValueError(
            f"design does not test this hypothesis: it varies {fields['manipulated']!r} and "
            f"measures {fields['measured']!r}"
        )

    known = {paper.id for paper in hypothesis.papers}
    named = [paper_id for paper_id in (payload.get("paper_ids") or []) if paper_id in known]
    if unknown := [pid for pid in (payload.get("paper_ids") or []) if pid not in known]:
        log.warning("experiment: dropping paper id(s) outside the hypothesis: %s", unknown)
    # Naming none of them is not grounds to drop the design — it just means the model did not
    # narrow, so the hypothesis's own papers stand as the grounding.
    papers = [paper for paper in hypothesis.papers if paper.id in named] or hypothesis.papers
    return ExperimentDesign(
        hypothesis=hypothesis, papers=papers, controlled=controlled, **fields
    )


# Three Claude calls per gap by the time one design comes out — assess the gap, propose the
# hypothesis, design the experiment — so this cap bites half again as hard as /hypotheses'
# does. The default matches that route's spend (3 gaps x 2 calls = 2 gaps x 3 calls = 6),
# and the ceiling keeps the worst case under it (5 x 3 = 15 against 10 x 2 = 20). A
# researcher reads designs one at a time anyway. ponytail: raise them when someone actually
# wants a batch, and background the route if they do.
EXPERIMENT_LIMIT = 2
MAX_EXPERIMENTS = 5


class ExperimentWorker:
    """Turns one hypothesis into one experiment testing it. The orchestrator registers by `name`."""

    name = "experiment"

    # Designing reasons over a hypothesis, not a question, so `run` takes the session and
    # the hypothesis — the same latitude HypothesisWorker's `run(session, gap)` takes. The
    # Worker protocol is structural and asks only for `name` + `run`; it stays satisfied.
    # `None` back means the design was refused (untestable, bad shape, API error) —
    # design_experiment's own contract, passed through rather than reinterpreted here.
    def run(
        self, session: Session, hypothesis: Hypothesis, **kwargs: Any
    ) -> ExperimentDesign | None:
        return design_experiment(session, hypothesis)
