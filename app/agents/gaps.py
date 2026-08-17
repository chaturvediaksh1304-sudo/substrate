import json
import logging
from dataclasses import dataclass

import anthropic
from sqlalchemy import desc, distinct, exists, func, select
from sqlalchemy.orm import Session, aliased

from app.agents.claude import MissingAPIKeyError, _client
from app.config import settings
from app.models import Concept, ConceptEdge

log = logging.getLogger(__name__)

# An open-triad search is a self-join over the edge list: cost grows with the sum of
# squared degrees, so a hub concept alone can produce thousands of pairs. Bounding the
# output is the cheap half of that; 50 is a page a human will actually read.
# ponytail: the join itself is unbounded — if it gets slow, cap by degree
# (skip concepts above N edges) before reaching for anything cleverer.
DEFAULT_LIMIT = 50


@dataclass
class StructuralGap:
    """Two concepts the literature ties to a common third, but never to each other.

    The pair is unordered — `concept_a` is simply the lower concept id, so (A, C) and
    (C, A) are one gap, reported once. `paper_ids` are the papers whose edges form the
    two legs: a gap whose legs come from different papers is a genuine disconnect in the
    literature, while a single-paper gap is often just how one abstract got parsed.
    """

    concept_a_id: int
    concept_a: str
    concept_b_id: int
    concept_b: str
    bridges: list[str]  # concepts linked to both endpoints
    bridge_count: int  # more independent bridges, stronger signal
    paper_ids: list[int]


def find_open_triads(
    session: Session, min_papers: int = 1, limit: int = DEFAULT_LIMIT
) -> list[StructuralGap]:
    """Concept pairs two hops apart with no direct edge — candidate gaps.

    `min_papers` demands the supporting edges span that many distinct papers. It defaults
    to 1 — no filtering — because a gap's cross-paper-ness is a ranking input (part 3),
    not a precondition, and a stricter default would silently return nothing on a small graph.

    An empty graph, or one with no open triads, is `[]` — a result, not an error.
    """
    # Both directions as one edge list, exactly as traversal does it: `A improves B`
    # links the two concepts whichever way the arrow points.
    undirected = (
        select(
            ConceptEdge.source_concept_id.label("src"),
            ConceptEdge.target_concept_id.label("dst"),
            ConceptEdge.paper_id.label("paper_id"),
        )
        .union_all(
            select(
                ConceptEdge.target_concept_id.label("src"),
                ConceptEdge.source_concept_id.label("dst"),
                ConceptEdge.paper_id.label("paper_id"),
            )
        )
        .cte("undirected")
    )
    leg, next_leg, closing = (undirected.alias(name) for name in ("leg", "next_leg", "closing"))
    bridge = aliased(Concept)

    # Least/greatest is the whole dedup: the triad is found once as A->B->C and again as
    # C->B->A, and both collapse onto the same group.
    low = func.least(leg.c.src, next_leg.c.dst).label("low_id")
    high = func.greatest(leg.c.src, next_leg.c.dst).label("high_id")

    pairs = (
        select(
            low,
            high,
            func.count(distinct(bridge.id)).label("bridge_count"),
            func.array_agg(distinct(bridge.name)).label("bridges"),
            # Both orientations are in the group, so each leg appears once as `leg` —
            # aggregating one side already covers both edges of the triad.
            func.array_agg(distinct(leg.c.paper_id)).label("paper_ids"),
        )
        .select_from(leg)
        .join(next_leg, next_leg.c.src == leg.c.dst)
        .join(bridge, bridge.id == leg.c.dst)
        .where(
            # A -> B -> A is a round trip, not a missing link.
            leg.c.src != next_leg.c.dst,
            # The link is only missing if it is missing in both directions — a C->A edge
            # closes a triad found as A->B->C. `closing` is the undirected view, so it is.
            ~exists(
                select(1)
                .select_from(closing)
                .where(closing.c.src == leg.c.src, closing.c.dst == next_leg.c.dst)
            ),
        )
        .group_by(low, high)
        .having(func.count(distinct(leg.c.paper_id)) >= min_papers)
        .order_by(desc("bridge_count"), low, high)
        .limit(limit)
        .subquery()
    )

    a, b = aliased(Concept), aliased(Concept)
    found = [
        StructuralGap(
            concept_a_id=row.low_id,
            concept_a=row.a_name,
            concept_b_id=row.high_id,
            concept_b=row.b_name,
            bridges=list(row.bridges),
            bridge_count=row.bridge_count,
            paper_ids=list(row.paper_ids),
        )
        for row in session.execute(
            select(
                pairs.c.low_id,
                pairs.c.high_id,
                pairs.c.bridge_count,
                pairs.c.bridges,
                pairs.c.paper_ids,
                a.name.label("a_name"),
                b.name.label("b_name"),
            )
            .join(a, a.id == pairs.c.low_id)
            .join(b, b.id == pairs.c.high_id)
            .order_by(desc(pairs.c.bridge_count), pairs.c.low_id, pairs.c.high_id)
        )
    ]
    log.info("gap detection: %d open triad(s), min_papers=%d", len(found), min_papers)
    return found


# A verdict plus a sentence of reasoning. Anything longer is the model writing an essay
# nobody asked for.
MAX_TOKENS = 500

JUDGE_SYSTEM = (
    "You decide whether competing claims from different papers about the same pair of "
    "concepts genuinely contradict each other.\n"
    "Reply with JSON only, in exactly this shape:\n"
    '{"verdict": "contradicts" or "compatible", "papers": [paper id, ...], '
    '"reasoning": "one or two sentences"}\n'
    "Judge the evidence sentences, not the relation labels. Different wording for the same "
    'finding ("improves", "accelerates") is compatible; opposite findings ("improves", '
    '"degrades") contradict; unrelated claims ("evaluated on", "outperforms") are compatible.\n'
    '"papers" lists the ids of the two or more papers whose claims conflict, and only ids '
    "shown to you. Leave it empty when the verdict is compatible.\n"
    "No prose, no markdown fences, no explanation outside the JSON."
)


@dataclass
class Claim:
    """One paper's assertion about an ordered concept pair, straight off the edge row."""

    paper_id: int
    relation: str
    evidence: str


@dataclass
class ClaimConflict:
    """Different papers asserting different relations about the same ordered concept pair.

    A candidate, not a finding: `improves` vs `accelerates` lands here too, and only the
    judge can tell that apart from `improves` vs `degrades`. The pair is **ordered** —
    "A improves B" and "B improves A" are different claims, not competing ones.
    ponytail: that means a genuine disagreement phrased in opposite directions
    ("A improves B" vs "B is degraded by A") is missed. Fix by unioning the reversed edge
    list in as context if extraction turns out to phrase claims both ways in practice.
    """

    source_concept_id: int
    source_concept: str
    target_concept_id: int
    target_concept: str
    claims: list[Claim]  # every competing claim, with the evidence the judge needs


@dataclass
class Contradiction:
    """A judged conflict. The facts are `conflict`'s rows; the model supplied only a verdict.

    `claims` is the subset of `conflict.claims` the model named as conflicting — resolved
    by paper id against the candidate, so a verdict naming a paper we never showed it is
    dropped rather than turned into a row.
    """

    conflict: ClaimConflict
    claims: list[Claim]
    reasoning: str


def find_conflicting_claims(session: Session, limit: int = DEFAULT_LIMIT) -> list[ClaimConflict]:
    """Ordered concept pairs where different papers assert different relations.

    The cheap structural filter: one grouped scan of the edge list narrows a large graph
    down to the handful of pairs worth spending a Claude call on. No LLM, no API key.

    Two papers asserting the *same* relation is corroboration, not conflict, and one paper
    asserting two relations is that paper being verbose — both are excluded by demanding
    two distinct papers *and* two distinct relations. Those two counts together are exactly
    "some two edges differ in both paper and relation": if every pair of edges shared one
    of the two, the graph could not have both counts above one.
    """
    pairs = (
        select(
            ConceptEdge.source_concept_id.label("src"),
            ConceptEdge.target_concept_id.label("dst"),
        )
        .group_by(ConceptEdge.source_concept_id, ConceptEdge.target_concept_id)
        .having(func.count(distinct(ConceptEdge.paper_id)) >= 2)
        .having(func.count(distinct(ConceptEdge.relation)) >= 2)
        .order_by(ConceptEdge.source_concept_id, ConceptEdge.target_concept_id)
        .limit(limit)
        .subquery()
    )

    source, target = aliased(Concept), aliased(Concept)
    conflicts: dict[tuple[int, int], ClaimConflict] = {}
    for edge, source_name, target_name in session.execute(
        select(ConceptEdge, source.name, target.name)
        .join(
            pairs,
            (pairs.c.src == ConceptEdge.source_concept_id)
            & (pairs.c.dst == ConceptEdge.target_concept_id),
        )
        .join(source, source.id == ConceptEdge.source_concept_id)
        .join(target, target.id == ConceptEdge.target_concept_id)
        .order_by(ConceptEdge.source_concept_id, ConceptEdge.target_concept_id, ConceptEdge.id)
    ):
        conflict = conflicts.setdefault(
            (edge.source_concept_id, edge.target_concept_id),
            ClaimConflict(
                source_concept_id=edge.source_concept_id,
                source_concept=source_name,
                target_concept_id=edge.target_concept_id,
                target_concept=target_name,
                claims=[],
            ),
        )
        conflict.claims.append(
            Claim(paper_id=edge.paper_id, relation=edge.relation, evidence=edge.evidence)
        )
    found = list(conflicts.values())
    log.info("conflict detection: %d candidate pair(s)", len(found))
    return found


def judge_contradictions(candidates: list[ClaimConflict]) -> list[Contradiction]:
    """Ask Claude which candidates genuinely contradict, and which merely differ.

    No session: a candidate already carries its papers, relations and evidence, so the
    judge reads the same rows the caller does — as `synthesize` reads its chunks.

    One call per candidate. Batching would save tokens and lose the isolation: a single
    malformed reply would take the whole batch down instead of one pair. Per Rules.md an
    API error, unparseable JSON or a bad shape is logged and skipped; a missing key is
    config, not data, and fails the whole run loudly.
    """
    found = []
    for candidate in candidates:
        try:
            contradiction = _judge(candidate)
        except MissingAPIKeyError:
            raise
        except Exception as exc:
            log.warning(
                "contradictions: skipping %s -> %s: %s",
                candidate.source_concept,
                candidate.target_concept,
                exc,
            )
            continue
        if contradiction is not None:
            found.append(contradiction)
    log.info(
        "contradiction detection: %d contradiction(s) from %d candidate(s)",
        len(found),
        len(candidates),
    )
    return found


def _judge(candidate: ClaimConflict) -> Contradiction | None:
    try:
        message = _client().messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": _prompt(candidate)}],
        )
    except anthropic.APIError as exc:
        raise RuntimeError(f"Anthropic API call failed: {exc}") from exc
    raw = "".join(block.text for block in message.content if block.type == "text")
    return _verdict(raw, candidate)


def _prompt(candidate: ClaimConflict) -> str:
    """Relation labels alone are not rulable on; the evidence sentence is why it is stored."""
    claims = "\n".join(
        f'- paper {claim.paper_id}: "{candidate.source_concept}" {claim.relation} '
        f'"{candidate.target_concept}"\n  evidence: {claim.evidence}'
        for claim in candidate.claims
    )
    return (
        f"Concept pair: {candidate.source_concept} -> {candidate.target_concept}\n\n"
        f"Competing claims:\n{claims}"
    )


def _verdict(raw: str, candidate: ClaimConflict) -> Contradiction | None:
    """The model proposes; this decides. Raises on anything the caller must not persist."""
    # Models wrap JSON in ``` fences out of habit, prompt or no prompt.
    payload = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))

    verdict = str(payload["verdict"]).strip().lower()
    if verdict not in ("contradicts", "compatible"):
        raise ValueError(f"unknown verdict {verdict!r}")
    if verdict == "compatible":
        return None

    by_paper = {claim.paper_id: claim for claim in candidate.claims}
    named = payload.get("papers") or []
    unknown = [paper_id for paper_id in named if paper_id not in by_paper]
    if unknown:
        raise ValueError(f"verdict names paper(s) outside the candidate: {unknown}")
    if len(named) < 2:
        raise ValueError(f"a contradiction needs two papers, verdict named {len(named)}")
    return Contradiction(
        conflict=candidate,
        claims=[by_paper[paper_id] for paper_id in named],
        reasoning=str(payload.get("reasoning", "")).strip(),
    )
