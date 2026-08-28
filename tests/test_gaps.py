from app.agents import gaps

# The graph-seeding helpers already exist one file over; a second copy would only rot.
from tests.test_graph_traversal import add_concept, add_paper, link


def pairs(found) -> list[tuple[str, str]]:
    return [(g.concept_a, g.concept_b) for g in found]


def open_triad(session, papers=1):
    """A -> B -> C with no A–C edge. `papers=2` puts each leg in its own paper."""
    p1 = add_paper(session, "1", "Paper One")
    p2 = add_paper(session, "2", "Paper Two") if papers == 2 else p1
    a, b, c = (add_concept(session, n) for n in ("A", "B", "C"))
    link(session, a, "improves", b, p1, "A improves B.")
    link(session, b, "improves", c, p2, "B improves C.")
    return {"papers": (p1, p2), "concepts": (a, b, c)}


# --- the signal ---------------------------------------------------------------


def test_an_open_triad_is_a_candidate_gap(db_session):
    seeded = open_triad(db_session)
    p1, _ = seeded["papers"]

    found = gaps.find_open_triads(db_session)

    assert len(found) == 1
    gap = found[0]
    assert (gap.concept_a, gap.concept_b) == ("A", "C")
    assert (gap.bridges, gap.bridge_count) == (["B"], 1)
    assert gap.paper_ids == [p1.id]


def test_an_empty_graph_has_no_gaps(db_session):
    assert gaps.find_open_triads(db_session) == []


# --- the missing link must really be missing, in either direction --------------


def test_a_closed_triad_is_not_a_gap(db_session):
    seeded = open_triad(db_session)
    p1, _ = seeded["papers"]
    a, _, c = seeded["concepts"]
    link(db_session, a, "builds on", c, p1, "A builds on C.")

    assert gaps.find_open_triads(db_session) == []


def test_a_triad_closed_in_the_opposite_direction_is_not_a_gap(db_session):
    """The closing edge is C->A while the triad was found as A->B->C. Still closed."""
    seeded = open_triad(db_session)
    p1, _ = seeded["papers"]
    a, _, c = seeded["concepts"]
    link(db_session, c, "builds on", a, p1, "C builds on A.")

    assert gaps.find_open_triads(db_session) == []


def test_legs_are_followed_in_both_directions(db_session):
    """B is the target of both edges; a source->target-only search would find nothing."""
    paper = add_paper(db_session, "1", "Paper One")
    a, b, c = (add_concept(db_session, n) for n in ("A", "B", "C"))
    link(db_session, a, "improves", b, paper)
    link(db_session, c, "improves", b, paper)

    assert pairs(gaps.find_open_triads(db_session)) == [("A", "C")]


# --- one gap per unordered pair ------------------------------------------------


def test_the_pair_is_reported_once_not_in_both_orders(db_session):
    open_triad(db_session)

    assert pairs(gaps.find_open_triads(db_session)) == [("A", "C")]


def test_a_self_pair_is_not_a_gap(db_session):
    """A -> B -> A is a round trip, not a missing link."""
    p1, p2 = add_paper(db_session, "1", "Paper One"), add_paper(db_session, "2", "Paper Two")
    a, b = add_concept(db_session, "A"), add_concept(db_session, "B")
    link(db_session, a, "improves", b, p1)
    link(db_session, b, "contradicts", a, p2)

    assert gaps.find_open_triads(db_session) == []


def test_several_bridges_are_one_gap_with_a_count(db_session):
    """A–B–C and A–D–C: two ways round, still one missing A–C link — a stronger signal."""
    paper = add_paper(db_session, "1", "Paper One")
    a, b, c, d = (add_concept(db_session, n) for n in ("A", "B", "C", "D"))
    link(db_session, a, "improves", b, paper)
    link(db_session, b, "improves", c, paper)
    link(db_session, a, "improves", d, paper)
    link(db_session, d, "improves", c, paper)

    found = gaps.find_open_triads(db_session)

    gap = next(g for g in found if (g.concept_a, g.concept_b) == ("A", "C"))
    assert gap.bridge_count == 2
    assert sorted(gap.bridges) == ["B", "D"]
    assert pairs(found).count(("A", "C")) == 1


# --- provenance ----------------------------------------------------------------


def test_a_gap_reports_the_papers_behind_both_legs(db_session):
    seeded = open_triad(db_session, papers=2)
    p1, p2 = seeded["papers"]

    gap = gaps.find_open_triads(db_session)[0]

    assert sorted(gap.paper_ids) == sorted([p1.id, p2.id])


def test_min_papers_keeps_a_cross_paper_gap(db_session):
    open_triad(db_session, papers=2)

    assert pairs(gaps.find_open_triads(db_session, min_papers=2)) == [("A", "C")]


def test_min_papers_drops_a_gap_from_a_single_paper(db_session):
    open_triad(db_session, papers=1)

    assert gaps.find_open_triads(db_session, min_papers=2) == []


# --- bounded -------------------------------------------------------------------


def test_limit_is_honoured(db_session):
    """A–B–C–D has two open triads: (A,C) bridged by B and (B,D) bridged by C."""
    paper = add_paper(db_session, "1", "Paper One")
    a, b, c, d = (add_concept(db_session, n) for n in ("A", "B", "C", "D"))
    link(db_session, a, "improves", b, paper)
    link(db_session, b, "improves", c, paper)
    link(db_session, c, "improves", d, paper)

    assert len(gaps.find_open_triads(db_session)) == 2
    assert len(gaps.find_open_triads(db_session, limit=1)) == 1


# --- a bridge that connects to everything tells you nothing ---------------------


def star(session, points, paper=None, hub="Hub", point="Point"):
    """One hub concept linked to `points` others and nothing else.

    Every pair of points is an open triad, so the hub alone manufactures
    `points * (points - 1) / 2` of them — the artifact the degree cap exists to drop.
    """
    paper = paper or add_paper(session, "1", "The Hub Paper")
    centre = add_concept(session, hub)
    for i in range(points):
        link(session, add_concept(session, f"{point} {i}"), "improves", centre, paper)
    return centre


def test_a_hub_bridge_is_dropped_and_the_ordinary_gap_survives(db_session):
    """Six points is fifteen hub-bridged pairs against one real one. Only the real one lands."""
    paper = add_paper(db_session, "1", "Paper One")
    star(db_session, points=6, paper=paper)
    x, b, y = (add_concept(db_session, n) for n in ("X", "B", "Y"))
    link(db_session, x, "improves", b, paper, "X improves B.")
    link(db_session, b, "improves", y, paper, "B improves Y.")

    assert pairs(gaps.find_open_triads(db_session)) == [("X", "Y")]


def test_a_hub_is_dropped_from_a_gap_a_real_bridge_also_supports(db_session):
    """The cap drops the leg, not the pair: X-Hub-Y and X-B-Y is still a gap, bridged by B."""
    paper = add_paper(db_session, "1", "Paper One")
    hub = star(db_session, points=4, paper=paper)
    x, b, y = (add_concept(db_session, n) for n in ("X", "B", "Y"))
    for near in (x, y):
        link(db_session, near, "improves", hub, paper, "Near improves the hub.")
        link(db_session, near, "improves", b, paper, "Near improves B.")

    found = gaps.find_open_triads(db_session)

    # The hub is barred from bridging, not from being an endpoint: (Hub, B) is a real gap,
    # bridged by X and Y, and it stays.
    gap = next(g for g in found if (g.concept_a, g.concept_b) == ("X", "Y"))
    assert (gap.bridges, gap.bridge_count) == (["B"], 1)
    assert all("Hub" not in g.bridges for g in found)


def test_a_bridge_at_the_degree_cap_still_bridges(db_session):
    """Three points: mean degree 1.5, so the cap is 3.0 and the hub sits exactly on it."""
    star(db_session, points=3)

    assert len(gaps.find_open_triads(db_session)) == 3


def test_a_bridge_one_edge_past_the_degree_cap_bridges_nothing(db_session):
    """Four points: mean degree 1.6, so the cap is 3.2 and a degree of 4 is over it."""
    star(db_session, points=4)

    assert gaps.find_open_triads(db_session) == []


# --- ubiquity, not degree, is what makes a bridge uninformative ----------------------------


def test_a_bridge_in_most_of_the_corpus_is_dropped(db_session):
    """A concept in a large fraction of papers says nothing about any two of its neighbours."""
    papers = [add_paper(db_session, f"p{i}", f"Paper {i}") for i in range(20)]
    ubiquitous = add_concept(db_session, "large language models")
    for i, paper in enumerate(papers[:16]):
        left = add_concept(db_session, f"left {i}")
        right = add_concept(db_session, f"right {i}")
        link(db_session, left, "uses", ubiquitous, paper)
        link(db_session, ubiquitous, "enables", right, paper)
    found = gaps.find_open_triads(db_session, limit=200)
    assert not any("large language models" in g.bridges for g in found)


def test_a_bridge_in_few_papers_with_ordinary_fan_out_still_bridges(db_session):
    """The case raw degree got backwards: a concept can carry several edges and still be
    specific, so long as they are not all one abstract's fan-out.

    A cap keyed on the graph's mean *degree* excluded this concept — it excluded 8 of the 11
    cross-paper concepts in the live graph. Ubiquity and fan-out together keep it: two papers,
    two edges each, is a narrow concept rather than either a hub or a verbose abstract.
    """
    papers = [add_paper(db_session, f"p{i}", f"Paper {i}") for i in range(20)]
    focused = add_concept(db_session, "model protein")
    a = add_concept(db_session, "discrete molecular dynamics")
    b = add_concept(db_session, "rosenbluth method")
    link(db_session, a, "simulates", focused, papers[0])
    link(db_session, focused, "is sampled by", b, papers[1])
    # Two more edges, one per paper: degree 4 over 2 papers is fan-out 2.0 — under the cap.
    link(db_session, focused, "relates to", add_concept(db_session, "lattice model"), papers[0])
    link(db_session, focused, "relates to", add_concept(db_session, "energy landscape"), papers[1])
    reported = {tuple(sorted((g.concept_a, g.concept_b))) for g in gaps.find_open_triads(db_session, limit=200)}
    assert ("discrete molecular dynamics", "rosenbluth method") in reported


def test_a_bridge_whose_edges_are_one_abstracts_fan_out_is_dropped(db_session):
    """Few papers is not enough to be a bridge — six edges from two papers is verbosity."""
    papers = [add_paper(db_session, f"p{i}", f"Paper {i}") for i in range(20)]
    verbose = add_concept(db_session, "system")
    for i in range(8):  # degree 8 over 2 papers = fan-out 4.0, past the cap
        link(db_session, verbose, "involves", add_concept(db_session, f"thing {i}"), papers[i % 2])
    assert not any("system" in g.bridges for g in gaps.find_open_triads(db_session, limit=200))


def test_a_bridge_in_a_couple_of_papers_still_bridges(db_session):
    papers = [add_paper(db_session, f"p{i}", f"Paper {i}") for i in range(10)]
    bridge = add_concept(db_session, "model protein")
    a, b = add_concept(db_session, "aaa"), add_concept(db_session, "bbb")
    link(db_session, a, "informs", bridge, papers[0])
    link(db_session, bridge, "informs", b, papers[1])
    assert any("model protein" in g.bridges for g in gaps.find_open_triads(db_session, limit=200))
