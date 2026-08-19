"""``analyst`` v10 must stay v9 plus upstream's own tail, byte for byte.

The two ANALYST lineages are content-disjoint. Both branch from v2 and share no text after it:
upstream's v3/v4 add result-shape, DISTINCT and star rules; this fork's v6-v9 add a ranking
clarification, ``basis`` and the language rules. The 2026-08-14 merge renumbered ours to v6-v9 and
kept upstream's v3-v5 beside them, which left the default (v9) carrying three rules upstream's
best arm never had and *missing* the three its measurements were for.

v10 is the composition, and it is written out in full because that is what every other variant in
``register/prompts.py`` does — a reader can see what a variant says without evaluating anything.
The cost of that convention is 4,908 duplicated characters that nothing stops from drifting, and
this file is what stops it: v10 is v9 concatenated with exactly the suffix v4 adds to v2. Edit v9
without re-grafting and this fails; hand-edit one of v10's shared paragraphs and this fails.

**It deliberately does not assert that v10 is better than v9, or that it is the default.** The
numbers behind upstream's tail (over-projection 107 -> 18, paired McNemar p=0.0008;
``r_star_projection`` 35/29 -> 2/2) were measured against upstream's v2, not against v9, and v9 is
a different base — among other things it makes the agent stop and ask on a ranking ambiguity,
which changes how often the result-shape rule is reached at all. Promoting v10 needs its own arm.
"""

from __future__ import annotations

from governed_bi.register.prompts import ANALYST


def _variants() -> dict[str, str]:
    return dict(ANALYST.variants)


def test_v10_is_v9_plus_exactly_the_suffix_v4_adds_to_v2() -> None:
    v = _variants()
    upstream_tail = v["v4"][len(v["v2"]) :]
    assert v["v4"].startswith(v["v2"]), (
        "upstream's v4 is no longer v2 plus a suffix, so 'the suffix v4 adds' is not a "
        "well-defined thing to graft; re-derive what v10 should be before touching this"
    )
    assert v["v10"] == v["v9"] + upstream_tail


def test_the_two_lineages_still_branch_from_v2_and_share_nothing_after_it() -> None:
    """The premise the renumbering rests on. If it stopped holding, v10 would be grafting a
    suffix onto a base that already contains part of it.
    """
    v = _variants()
    assert v["v3"].startswith(v["v2"])
    assert v["v4"].startswith(v["v3"])
    for ours in ("v6", "v7", "v8", "v9"):
        assert not v[ours].startswith(v["v3"]), (
            f"{ours} now begins with upstream's v3, so the two lineages are no longer disjoint"
        )
        assert "The result table is the answer" not in v[ours], (
            f"{ours} carries upstream's result-shape rule directly; v10's graft would duplicate it"
        )


def test_v10_carries_both_sides_rules() -> None:
    """A cheap check that the graft did not silently produce v9 or v4 again."""
    v = _variants()
    assert "CRITICAL LANGUAGE RULE" in v["v10"], "this fork's language rule is missing"
    assert 'basis="ranking_ambiguity"' in v["v10"], "this fork's ranking clarification is missing"
    assert "The result table is the answer" in v["v10"], "upstream's result-shape rule is missing"
    assert "Choose DISTINCT on what the question means" in v["v10"], "upstream's DISTINCT rule"
    assert "A bare star in the select list is refused" in v["v10"], "upstream's star rule"
    assert len(v["v10"]) > len(v["v9"]) and len(v["v10"]) > len(v["v4"])


def test_v10_is_not_the_default() -> None:
    """Promoting it is a measurement, not an edit — see this module's docstring."""
    assert ANALYST.default == "v9", (
        "v10 became the default. That is only correct with a measured arm behind it: upstream's "
        "numbers for the grafted tail were taken against its own v2 base, not against v9."
    )
