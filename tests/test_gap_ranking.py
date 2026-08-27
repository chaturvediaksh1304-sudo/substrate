import json
import logging

import anthropic
import httpx
import pytest

from app.agents import gaps
from app.agents.claude import MissingAPIKeyError
from app.config import settings

# The fake Anthropic client and the graph-seeding helpers each already exist one file over;
# a second copy of either would only rot.
from tests.test_contradictions import fake_client
from tests.test_graph_traversal import add_concept, add_paper, link

IMPROVES = "Distillation improves accuracy on every benchmark we tried."
DEGRADES = "Distillation degrades accuracy under distribution shift."


def assessment(verdict="real_gap", significance=2, rationale="Nobody has joined these up.", **extra) -> str:
    return json.dumps(
        {"verdict": verdict, "significance": significance, "rationale": rationale, **extra}
    )


def ranked(found) -> list[tuple[str, int, int]]:
    return [(g.kind, g.concept_a_id, g.concept_b_id) for g in found]


def mixed_graph(session):
    """One cross-paper disagreement and one open triad, in three separate papers.

    The conflict's concepts are created first, so they hold the lower ids while both
    candidates prescore the same — the ranked order therefore depends on the concept-id
    tiebreak, not on the order the two searches happen to run in.
    """
    bridge_paper = add_paper(session, "1", "Bridges of Konigsberg")
    pro = add_paper(session, "2", "Distillation Improves Accuracy")
    con = add_paper(session, "3", "Distillation Degrades Accuracy")
    d, e = add_concept(session, "Distillation"), add_concept(session, "Accuracy")
    link(session, d, "improves", e, pro, IMPROVES)
    link(session, d, "degrades", e, con, DEGRADES)
    a, b, c = (add_concept(session, n) for n in ("A", "B", "C"))
    link(session, a, "improves", b, bridge_paper, "A improves B.")
    link(session, b, "improves", c, bridge_paper, "B improves C.")
    return {"papers": (bridge_paper, pro, con), "concepts": (d, e, a, b, c)}


def ring(session, points):
    """A cycle of concepts: each neighbour pair of a concept is an open triad, so `points`
    concepts give `points` candidates — and no concept is a hub, which a star would be.
    `find_open_triads` bars a bridge above twice the graph's mean degree, and every concept
    on a ring sits exactly on the mean.
    """
    paper = add_paper(session, "1", "The Ring Paper")
    concepts = [add_concept(session, f"Point {i}") for i in range(points)]
    for i, point in enumerate(concepts):
        nxt = concepts[(i + 1) % points]
        link(session, point, "improves", nxt, paper, f"Point {i} improves point {i + 1}.")


# --- the deterministic half, no API key involved -------------------------------


def test_the_prescore_ranks_better_supported_candidates_first_without_any_model(db_session):
    """Half of ranking needs no key at all, so it is tested on its own, with no client."""
    p1, p2, p3 = (add_paper(db_session, str(i), f"Paper {i}") for i in (1, 2, 3))
    # Two independent bridges across two papers: A-B-C and A-D-C, no A-C edge.
    # B and D are two hops apart via A and C too, so this seeds two 4-scoring gaps.
    a, b, c, d = (add_concept(db_session, n) for n in ("A", "B", "C", "D"))
    link(db_session, a, "improves", b, p1, "A improves B.")
    link(db_session, b, "improves", c, p2, "B improves C.")
    link(db_session, a, "improves", d, p1, "A improves D.")
    link(db_session, d, "improves", c, p2, "D improves C.")
    # Three papers disagreeing about one pair.
    e, f = add_concept(db_session, "E"), add_concept(db_session, "F")
    link(db_session, e, "improves", f, p1, "E improves F.")
    link(db_session, e, "degrades", f, p2, "E degrades F.")
    link(db_session, e, "has no effect on", f, p3, "E has no effect on F.")
    # One bridge, one paper — the weakest signal in the graph.
    g, h, i = (add_concept(db_session, n) for n in ("G", "H", "I"))
    link(db_session, g, "improves", h, p3, "G improves H.")
    link(db_session, h, "improves", i, p3, "H improves I.")

    candidates = gaps._candidates(db_session)

    assert [(c.kind, c.prescore) for c in candidates] == [
        ("missing_link", 4),
        ("missing_link", 4),
        ("contradiction", 3),
        ("missing_link", 2),
    ]
    assert (candidates[0].concept_a_id, candidates[0].concept_b_id) == (a.id, c.id)
    assert [g.significance for g in candidates] == [0, 0, 0, 0]


def test_claude_is_called_at_most_limit_times_however_many_candidates_exist(db_session, monkeypatch):
    """The cost property: spend follows `limit`, not the size of the graph."""
    ring(db_session, points=6)
    messages = fake_client(monkeypatch, assessment())

    assert len(gaps._candidates(db_session)) == 6

    found = gaps.rank_gaps(db_session, limit=3)

    assert len(messages.calls) == 3
    assert len(found) == 3


# --- one structured list ------------------------------------------------------


def test_both_signal_types_land_in_one_ranked_list(db_session, monkeypatch):
    seeded = mixed_graph(db_session)
    d, e, a, _, c = seeded["concepts"]
    messages = fake_client(monkeypatch, assessment())

    found = gaps.rank_gaps(db_session)

    assert ranked(found) == [("contradiction", d.id, e.id), ("missing_link", a.id, c.id)]
    assert [g.significance for g in found] == [2, 2]
    assert all(g.rationale == "Nobody has joined these up." for g in found)
    # The model cannot rule on a relation label; it is shown the papers' own sentences.
    conflict_prompt = messages.calls[0]["messages"][0]["content"]
    assert IMPROVES in conflict_prompt and DEGRADES in conflict_prompt


def test_paper_titles_are_hydrated_onto_the_right_gap(db_session, monkeypatch):
    """'Papers 11 and 15 disagree' is a graph stat; naming them is output a human can use."""
    seeded = mixed_graph(db_session)
    bridge_paper, pro, con = seeded["papers"]
    fake_client(monkeypatch, assessment())

    by_kind = {g.kind: g for g in gaps.rank_gaps(db_session)}

    assert [(p.id, p.title) for p in by_kind["missing_link"].papers] == [
        (bridge_paper.id, "Bridges of Konigsberg")
    ]
    assert [(p.id, p.title) for p in by_kind["contradiction"].papers] == [
        (pro.id, "Distillation Improves Accuracy"),
        (con.id, "Distillation Degrades Accuracy"),
    ]
    assert by_kind["missing_link"].evidence == ["B"]
    assert sorted(by_kind["contradiction"].evidence) == sorted([IMPROVES, DEGRADES])


def test_the_same_graph_and_verdicts_rank_the_same_way_twice(db_session, monkeypatch):
    """Part 4 serves this list over HTTP; a shuffling list is not testable."""
    seeded = mixed_graph(db_session)
    d, e, a, _, c = seeded["concepts"]
    fake_client(monkeypatch, assessment())

    first, second = gaps.rank_gaps(db_session), gaps.rank_gaps(db_session)

    assert ranked(first) == ranked(second)
    assert ranked(first) == [("contradiction", d.id, e.id), ("missing_link", a.id, c.id)]


def test_the_models_significance_outranks_the_prescore(db_session, monkeypatch):
    """The prescore picks who gets asked; the answer, not the arithmetic, decides the order."""
    seeded = mixed_graph(db_session)
    d, e, a, _, c = seeded["concepts"]
    # The conflict is assessed first — same prescore, lower concept ids — but rated lower.
    fake_client(monkeypatch, assessment(significance=1), assessment(significance=3))

    assert ranked(gaps.rank_gaps(db_session)) == [
        ("missing_link", a.id, c.id),
        ("contradiction", d.id, e.id),
    ]


# --- the model judges, the rows supply the facts -------------------------------


def test_a_candidate_the_model_rejects_is_dropped(db_session, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    mixed_graph(db_session)
    fake_client(
        monkeypatch,
        assessment(verdict="not_a_gap", rationale="The two papers measure different things."),
        assessment(),
    )

    found = gaps.rank_gaps(db_session)

    assert [g.kind for g in found] == ["missing_link"]
    assert "not a real gap" in caplog.text


def test_a_model_naming_a_concept_or_paper_we_never_showed_it_fabricates_nothing(
    db_session, monkeypatch
):
    mixed_graph(db_session)
    fake_client(
        monkeypatch,
        assessment(
            concepts=["Quantum Foam", "Sourdough"],
            papers=[{"id": 9999, "title": "A Paper That Does Not Exist"}],
            evidence=["We made this sentence up."],
        ),
    )

    found = gaps.rank_gaps(db_session)

    assert {g.concept_a for g in found} == {"Distillation", "A"}
    assert all(p.id != 9999 for g in found for p in g.papers)
    assert "A Paper That Does Not Exist" not in {p.title for g in found for p in g.papers}
    assert "We made this sentence up." not in [ev for g in found for ev in g.evidence]


@pytest.mark.parametrize(
    "reply",
    [
        assessment(significance=9),
        assessment(significance=0),
        assessment(significance="high"),
        assessment(verdict="probably"),
    ],
)
def test_a_bad_shape_is_logged_and_skipped_never_coerced(db_session, monkeypatch, caplog, reply):
    mixed_graph(db_session)
    fake_client(monkeypatch, reply)

    assert gaps.rank_gaps(db_session) == []
    assert "gap ranking: skipping" in caplog.text


def test_one_failing_assessment_is_skipped_and_the_rest_still_rank(db_session, monkeypatch):
    mixed_graph(db_session)
    fake_client(
        monkeypatch,
        anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")),
        assessment(),
    )

    found = gaps.rank_gaps(db_session)

    assert [g.kind for g in found] == ["missing_link"]


def test_missing_api_key_still_fails_loud(db_session, monkeypatch):
    """Rules.md reserves hard failure for missing keys — no silently empty gap list."""
    mixed_graph(db_session)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")

    with pytest.raises(MissingAPIKeyError):
        gaps.rank_gaps(db_session)


def test_an_empty_graph_ranks_nothing(db_session):
    assert gaps.rank_gaps(db_session) == []
