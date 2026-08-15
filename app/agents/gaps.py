import logging
from dataclasses import dataclass

from sqlalchemy import desc, distinct, exists, func, select
from sqlalchemy.orm import Session, aliased

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
