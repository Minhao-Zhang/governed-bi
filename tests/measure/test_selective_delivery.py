"""Acceptance tests for the risk-coverage instrument.

Every assertion here is against a **hand-computed literal** over a fixture small enough
to work out on paper. That rule is not style: ``docs/open-work.md`` §3.9 found eight
tests in this repository that survived mutation because they asserted that a constant
equals itself, and the shape they all share is re-deriving the answer from the same
expression the code uses. So no test below calls the module twice and compares the
results, except where the *point* is that two computations must agree (the constant
signal against the no-ranking reference), and in that case one of the two is a literal.

The properties under test, each attached to a defect this instrument could plausibly
have shipped with — and the last three to one it did:

1. **Ties are averaged.** A curve that walks tied rows in artifact order is a curve
   whose interior reports the driver's write order. Caught by a fixture whose signal is
   constant: its AURC must equal the no-ranking reference *exactly*.
2. **Absent is not zero** (L-R1). A signal missing on a delivered turn must make the
   curve unavailable, not rank that turn at 0.
3. **The grade never reaches a signal.** Checked two ways: an **allowlist** of the
   fields a signal may read at all, and the older probe that moves the grade. The
   allowlist is the general form and it exists because the probe alone had a hole —
   ``computed_fingerprint`` is on every real row and was not among the seven fields the
   probe moved, so a signal reading it passed.
4. **A rate carries its denominator.** ``PricedAbstention`` must refuse a numerator and
   a denominator that were filtered differently, must not fold ``computed_correct`` into
   ``correct``, and must not hand back a bare float — the rate it derives stores two
   integers and no rate, so a caller holding the number holds the denominator.
5. **A deterministic comparison is not a hypothesis test.** Every risk-coverage trade is
   nested by construction: a ranking reorders the turns the engine already agreed to
   answer, so one discordant cell is 0 as arithmetic and the p-value restates the subset
   relation. ``compare_policies`` returns ``NestedPolicies`` there and a real
   ``McNemarResult`` only where the two sets cross.
6. **A figure names the k it was read at.** The curve at a requested coverage and the
   largest realisable policy within it are different k and different numbers; the page
   that quoted both attached the second's consequence to the first.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from governed_bi.measure.abstention import PricedAbstention
from governed_bi.measure.population import Population
from governed_bi.measure.selective import (
    DeliveryPolicy,
    NestedPolicies,
    compare_policies,
    engine_policy,
    graded,
    no_ranking,
    oracle,
    risk_coverage,
)
from governed_bi.measure.signals import (
    READABLE_FIELDS,
    SIGNALS,
    Direction,
    Signal,
    assert_no_signal_reads_the_grade,
)
from governed_bi.measure.stats import McNemarResult
from governed_bi.register.quantity import NotMeasured


def _row(qid: str, *, outcome: str = "answered", correct: bool | None = True, **extra: object):
    row: dict[str, object] = {"question_id": qid, "outcome": outcome, "correct": correct}
    row.update(extra)
    return row


def _tokens(n: int) -> list[dict[str, object]]:
    return [{"stage": "agent_core", "output_tokens": n, "input_tokens": 10, "model_calls": 1}]


def _arm(rows: list[dict[str, object]]) -> Population:
    return graded(Population.of("fixture", rows))


#: Four delivered turns, two right and two wrong, all carrying the *same* token count,
#: plus one decline. Hand-computed below; the tie group is the whole delivered set.
_ALL_TIED = [
    _row("a", correct=True, usage=_tokens(100)),
    _row("b", correct=False, usage=_tokens(100)),
    _row("c", correct=True, usage=_tokens(100)),
    _row("d", correct=False, usage=_tokens(100)),
    _row("e", outcome="refused", correct=False, usage=[]),
]


# ── 1. ties are averaged, not walked in file order ────────────────────────────


def test_a_signal_that_cannot_separate_gives_exactly_the_no_ranking_curve() -> None:
    """Four tied turns, two wrong. Every prefix must read 0.5 accuracy, exactly.

    Worked by hand: the tie group holds 2 errors over 4 turns, so after k delivered
    turns the expected error count is ``2k/4``, risk is ``0.5`` for k = 1..4, and the
    decline at k = 5 makes it ``3/5``. AURC is the mean of those five risks:
    ``(0.5 + 0.5 + 0.5 + 0.5 + 0.6) / 5 = 0.52``.

    Under artifact-order tie-breaking the same fixture gives risks
    ``0, 0.5, 1/3, 0.5, 0.6`` and an AURC of ``0.3867`` -- a signal that separates
    nothing would look like the best one on the page.
    """
    arm = _arm(_ALL_TIED)
    curve = risk_coverage(arm, SIGNALS["agent_out_tok"])
    assert curve.errors == (0.5, 1.0, 1.5, 2.0, 3.0)
    assert curve.aurc.value == pytest.approx(0.52)
    assert curve.aurc.value == pytest.approx(no_ranking(arm).aurc.value)
    # And the AUC agrees: one distinct value cannot discriminate, so mid-ranks put it at
    # exactly 0.5. Without mid-ranks the same fixture scores 0.25 or 0.75 depending on
    # which of the two right answers the driver wrote first.
    assert curve.auc.value == pytest.approx(0.5)


def test_a_tie_group_is_one_operating_point_and_a_policy_cannot_split_it() -> None:
    """Asking for 40% coverage of a single 4-way tie must deliver nothing.

    Delivering two of four turns the signal cannot tell apart is a coin flip dressed as
    a policy, so the cut moves down to the boundary below -- which is the start of the
    ranking. The empty policy is the honest answer and its accuracy is unmeasured, not
    zero: no answers were given, so there is no accuracy to report.
    """
    curve = risk_coverage(_arm(_ALL_TIED), SIGNALS["agent_out_tok"])
    assert curve.realisable_coverages() == (0.8,)
    assert curve.policy_at_most(0.4).delivered == frozenset()
    with pytest.raises(NotMeasured):
        curve.policy_at_most(0.4).point().accuracy.value


def test_a_separating_signal_produces_the_hand_computed_curve() -> None:
    """Two right turns cheap, two wrong turns expensive, one decline.

    Ranked 10, 20, 30, 40 then the decline. Cumulative errors ``0, 0, 1, 2, 3``; risks
    ``0, 0, 1/3, 0.5, 0.6``; AURC ``(0 + 0 + 1/3 + 0.5 + 0.6)/5 = 0.28667``.
    """
    curve = risk_coverage(
        _arm(
            [
                _row("a", correct=True, usage=_tokens(10)),
                _row("b", correct=True, usage=_tokens(20)),
                _row("c", correct=False, usage=_tokens(30)),
                _row("d", correct=False, usage=_tokens(40)),
                _row("e", outcome="capped", correct=False, usage=[]),
            ]
        ),
        SIGNALS["agent_out_tok"],
    )
    assert curve.errors == (0.0, 0.0, 1.0, 2.0, 3.0)
    assert curve.aurc.value == pytest.approx(1.4333333333333333 / 5)
    assert curve.accuracy_at(0.8).accuracy.value == pytest.approx(0.5)
    assert curve.auc.value == pytest.approx(0.0)  # raw direction: more tokens, more right = never


def test_the_declared_direction_is_applied_and_not_fitted() -> None:
    """Flip which turns are right; the curve must get *worse*, not silently re-sign.

    A ranker that chooses its sign per arm cannot lose, and would report the same AURC
    on both fixtures. Here the same four token counts are paired with the opposite
    grades, so the declared "fewer tokens is safer" claim is now false and the curve
    must show it: AURC goes from 0.2867 to 0.7533 while the raw AUC goes 0.0 to 1.0.
    """
    inverted = risk_coverage(
        _arm(
            [
                _row("a", correct=False, usage=_tokens(10)),
                _row("b", correct=False, usage=_tokens(20)),
                _row("c", correct=True, usage=_tokens(30)),
                _row("d", correct=True, usage=_tokens(40)),
                _row("e", outcome="capped", correct=False, usage=[]),
            ]
        ),
        SIGNALS["agent_out_tok"],
    )
    assert inverted.errors == (1.0, 2.0, 2.0, 2.0, 3.0)
    assert inverted.aurc.value == pytest.approx(3.7666666666666666 / 5)
    assert inverted.auc.value == pytest.approx(1.0)


# ── 2. absent is not zero ─────────────────────────────────────────────────────


def test_one_delivered_turn_without_the_signal_makes_the_whole_curve_unavailable() -> None:
    """Not a curve over the other two. An AURC over a sub-population is a different
    number wearing the same name, and the report puts them in one column."""
    curve = risk_coverage(
        _arm(
            [
                _row("a", correct=True, usage=_tokens(10)),
                _row("b", correct=False, usage=_tokens(20)),
                _row("c", correct=True, usage=[]),
            ]
        ),
        SIGNALS["agent_out_tok"],
    )
    assert curve.ranked == ()
    assert "1 of 3 delivered turns" in curve.unavailable
    with pytest.raises(NotMeasured):
        curve.aurc.value


def test_an_absent_ledger_is_unmeasured_and_an_empty_one_is_zero() -> None:
    """The exact distinction run1 lost. ``attempts`` is absent on all 1,351 of its rows,
    and an empty ledger is a real state on the two v4 turns that answered with no SQL.

    Collapsing them gives run1 a constant ``n_attempts`` with a tidy AUC of 0.5000 --
    "the ledger carries nothing", when the ledger was never written.
    """
    ledger = SIGNALS["n_attempts"]
    assert ledger.read({"attempts": []}) == 0.0
    assert ledger.read({"attempts": [{"passed": True}]}) == 1.0
    assert ledger.read({}) is None
    assert SIGNALS["n_failed_attempts"].read({}) is None


def test_a_turn_that_is_neither_answered_nor_a_declared_abstention_is_refused() -> None:
    """A third kind of ending has to be classified before coverage means anything."""
    arm = Population.of("odd", [_row("a", outcome="mystery", correct=False)])
    with pytest.raises(ValueError, match="neither an answer nor a declared abstention"):
        engine_policy(arm)


def test_crashes_and_ungraded_turns_leave_the_population_with_their_filters_named() -> None:
    """A crash must not be counted as an abstention: it is not a decision.

    ``correct is None`` leaves too -- ``docs/measurement.md`` says a grade the harness
    could not make is not a wrong answer.
    """
    arm = graded(
        Population.of(
            "mixed",
            [
                _row("a", correct=True, usage=_tokens(10)),
                _row("b", outcome="crashed", correct=False),
                _row("c", correct=None, usage=_tokens(10)),
                _row("d", outcome="refused", correct=False),
            ],
        )
    )
    assert arm.units == {"a", "d"}
    assert arm.filtered_by == (
        "excluded crashed turns",
        "excluded turns the grader could not judge",
    )
    assert engine_policy(arm).point().coverage.value == pytest.approx(0.5)


# ── 3. the grade never reaches a signal ───────────────────────────────────────


def test_the_leakage_guard_fires_on_a_signal_that_reads_the_grade() -> None:
    """The guard must be able to fail, or its import-time call proves nothing."""
    rogue = Signal(
        name="peeks",
        direction=Direction.higher_first,
        why="reads the answer, which is the whole point of this test",
        read=lambda row: 1.0 if row.get("correct") else 0.0,
    )
    assert_no_signal_reads_the_grade(SIGNALS)  # the real registry is clean
    with pytest.raises(AssertionError, match="peeks"):
        assert_no_signal_reads_the_grade({**SIGNALS, "peeks": rogue})


def test_the_guard_catches_a_grade_field_the_probe_row_never_thought_of() -> None:
    """**The named hole.** The old guard moved seven grade-bearing fields and required each
    signal to return the same value -- a denylist of what the probe *remembered*.

    ``computed_fingerprint`` is on every real row and is what ``computed_correct`` is derived
    from, and it was not among the seven. A signal reading it returned the same value on both
    probes, passed, and would have ranked turns by the counterfactual grade. The allowlist
    catches it by name whether or not the probe row carries it, which is the general form.
    """
    rogue = Signal(
        name="peeks_at_the_fingerprint",
        direction=Direction.higher_first,
        why="reads a field the moved-grade probe forgot; this is the demonstrated hole",
        read=lambda row: float(len(str(row.get("computed_fingerprint") or ""))),
    )
    with pytest.raises(AssertionError, match="computed_fingerprint"):
        assert_no_signal_reads_the_grade({**SIGNALS, "peeks_at_the_fingerprint": rogue})

    # Any unlisted field, not just a grade one: an allowlist is what makes that automatic.
    invented = Signal(
        name="reads_an_unlisted_field",
        direction=Direction.lower_first,
        why="a field nobody has classified is a field nobody has thought about",
        read=lambda row: float(len(str(row.get("gold_result_rows") or ""))),
    )
    with pytest.raises(AssertionError, match="gold_result_rows"):
        assert_no_signal_reads_the_grade({**SIGNALS, "reads_an_unlisted_field": invented})


def test_the_allowlist_itself_cannot_be_widened_to_the_answer() -> None:
    """The second lock. An allowlist is only as good as the review of what goes in it, so
    the names in it are checked against the grade vocabulary at import."""
    import governed_bi.measure.signals as signals_module

    assert not READABLE_FIELDS & {"correct", "computed_correct", "computed_fingerprint"}
    original = signals_module.READABLE_FIELDS
    try:
        signals_module.READABLE_FIELDS = original | {"computed_correct"}
        with pytest.raises(AssertionError, match="READABLE_FIELDS"):
            assert_no_signal_reads_the_grade(SIGNALS)
    finally:
        signals_module.READABLE_FIELDS = original


def test_the_oracle_is_the_ceiling_and_is_labelled_as_reading_gold() -> None:
    """It exists so the curve has a scale, and it must be impossible to mistake for a
    deployable ranker: two right and two wrong turns give perfect accuracy to 50%."""
    curve = oracle(
        _arm(
            [
                _row("a", correct=True, usage=_tokens(40)),
                _row("b", correct=False, usage=_tokens(10)),
                _row("c", correct=True, usage=_tokens(30)),
                _row("d", correct=False, usage=_tokens(20)),
            ]
        )
    )
    assert "reads the grade" in curve.signal
    assert curve.errors == (0.0, 0.0, 1.0, 2.0)
    assert curve.accuracy_at(0.5).accuracy.value == pytest.approx(1.0)


# ── 4. a rate carries its denominator ─────────────────────────────────────────


def test_abstention_precision_is_over_the_priced_subset_and_says_so() -> None:
    """Three declines, two priceable, one of those would have been right.

    The rate is 1/2 over the priced pair and not 2/3 over the declines, and
    ``unpriceable`` carries the third. This is §4.1's 62-of-73 in miniature.
    """
    priced = PricedAbstention.of(
        _arm(
            [
                _row("a", correct=True, usage=_tokens(10)),
                _row("b", outcome="refused", correct=False, computed_correct=False),
                _row("c", outcome="capped", correct=False, computed_correct=True),
                _row("d", outcome="clarification", correct=False, computed_correct=None),
            ]
        )
    )
    assert (priced.declined.n, priced.priced.n, priced.unpriceable) == (3, 2, 1)
    wrong = priced.would_have_been_wrong
    assert (wrong.wrong, wrong.priced) == (1, 2)
    assert wrong.share(2).value == pytest.approx(0.5)
    assert "n=2" in priced.render() and "1 of 3 decline(s)" in priced.render()


def test_the_rate_has_no_attribute_that_hands_it_over_without_the_denominator() -> None:
    """The published claim, made true rather than narrowed.

    It read: "the rate cannot be obtained without its denominator: the type stores two
    ``Population``s and no float". That was true of ``PricedAbstention`` and false of what it
    returned -- ``.would_have_been_wrong.value`` was ``0.7741935483870968`` and ``.render(4)``
    was ``"0.7742"``, both bare, and nothing forced a caller through the object's own
    ``render()``.

    Now the derived object stores two integers and no rate either. There is no attribute that
    is the float, ``render`` prints both numbers, and ``share`` demands the denominator back --
    so a caller that has the rate has necessarily read the 62 it is over.
    """
    wrong = PricedAbstention.of(
        _arm(
            [
                _row("a", correct=True, usage=_tokens(10)),
                _row("b", outcome="refused", correct=False, computed_correct=False),
                _row("c", outcome="capped", correct=False, computed_correct=True),
            ]
        )
    ).would_have_been_wrong

    floats = [
        name
        for name in dir(wrong)
        if not name.startswith("_") and isinstance(getattr(wrong, name, None), float)
    ]
    assert not floats, f"{floats} hand back a bare rate; the point is that none does"
    assert "0.5000 (1/2)" in wrong.render(), "render carries both numbers, always"

    # And the denominator cannot be swapped for a wider one on the way out. Quoting the
    # priced-subset rate as though it were over every decline is §4.1's whole subject.
    with pytest.raises(ValueError, match="never met"):
        wrong.share(3)


def test_no_priceable_decline_leaves_the_rate_unmeasured_rather_than_perfect() -> None:
    """Both runs of the null replicate pair are in this state: 162 and 176 declines,
    none of them priced. A rate of 1.0 there would read as flawless abstention."""
    priced = PricedAbstention.of(
        _arm(
            [
                _row("a", correct=True, usage=_tokens(10)),
                _row("b", outcome="refused", correct=False),
            ]
        )
    )
    assert priced.unpriceable == 1
    wrong = priced.would_have_been_wrong
    assert not wrong.is_measured and wrong.wrong is None
    assert "not measured" in wrong.render()
    with pytest.raises(NotMeasured):
        wrong.share(wrong.priced).value


def test_a_numerator_and_denominator_that_never_met_are_refused() -> None:
    """The whole reason the type holds two populations instead of a float."""
    arm = _arm(
        [
            _row("a", outcome="refused", correct=False, computed_correct=False),
            _row("b", correct=True, usage=_tokens(10)),
        ]
    )
    declined = arm.restrict(lambda r: r.get("outcome") == "refused", "declined turns only")
    with pytest.raises(ValueError, match="not a restriction"):
        PricedAbstention(declined=declined, priced=arm)


def test_a_priced_abstention_never_credits_the_engine_for_a_withheld_answer() -> None:
    """``computed_correct`` must not leak into the operating point.

    A decline whose statement would have been right stays wrong in ``correct``, so
    coverage and selective accuracy do not move. Folding the two is how an engine that
    commits to nothing scores well.
    """
    arm = _arm(
        [
            _row("a", correct=True, usage=_tokens(10)),
            _row("b", correct=False, usage=_tokens(20)),
            _row("c", outcome="capped", correct=False, computed_correct=True),
        ]
    )
    point = engine_policy(arm).point()
    assert (point.delivered, point.correct) == (2, 1.0)
    assert point.accuracy.value == pytest.approx(0.5)
    wrong = PricedAbstention.of(arm).would_have_been_wrong
    assert (wrong.wrong, wrong.priced) == (0, 1)
    assert wrong.share(1).value == pytest.approx(0.0)


# ── comparisons go through the one McNemar ────────────────────────────────────


def test_a_nested_trade_is_reported_as_arithmetic_and_not_as_a_test() -> None:
    """Engine delivers 4 (3 right); the ranked policy delivers 2 (both right).

    The ranked policy hands back a **subset** of the engine's turns and changes no turn's
    grade, so ``useful_ranked`` is a subset of ``useful_engine`` by construction: the
    discordant cell one way is 0 as arithmetic, and the p-value is a function of the other
    cell alone. On ``v4`` that shape was published as ``only_engine=162, only_ranked=0,
    delta=-0.1199, MDE=0.0264, p=3.4e-49`` -- decisive, and unable to have come out any
    other way.

    The substantive claim survives and is what this returns: the trade costs one right
    answer here, 162 on ``v4``. What is refused is the inferential dress, because a reader
    who sees a p-value is entitled to think a null was tested. Before the fix this returned
    a ``McNemarResult`` with ``only_b = 0``, which is why that literal is asserted absent.
    """
    arm = _arm(
        [
            _row("a", correct=True, usage=_tokens(10)),
            _row("b", correct=True, usage=_tokens(20)),
            _row("c", correct=True, usage=_tokens(30)),
            _row("d", correct=False, usage=_tokens(40)),
        ]
    )
    engine = engine_policy(arm)
    ranked = risk_coverage(arm, SIGNALS["agent_out_tok"]).policy_at_most(0.5)
    assert (engine.useful, ranked.useful) == (3, 2)

    result = compare_policies(engine, ranked)
    assert isinstance(result, NestedPolicies), "a nested pair must not be dressed as a test"
    assert (result.useful_wider, result.useful_narrower, result.lost) == (3, 2, 1)
    assert result.withheld == 2 and result.n_pairs == 4
    assert not result.is_decisive, "there is no inference here to be decisive about"
    rendered = result.render()
    assert "1 fewer useful answer" in rendered and "No paired test is reported" in rendered
    assert "p=" not in rendered and "MDE" not in rendered


def test_the_order_of_a_nested_pair_does_not_change_what_is_reported() -> None:
    """Which side the caller passes first is a presentation choice, not a finding."""
    arm = _arm(
        [
            _row("a", correct=True, usage=_tokens(10)),
            _row("b", correct=False, usage=_tokens(20)),
        ]
    )
    engine = engine_policy(arm)
    narrower = DeliveryPolicy(label="narrower", population=arm, delivered=frozenset({"a"}))

    for pair in ((engine, narrower), (narrower, engine)):
        result = compare_policies(*pair)
        assert isinstance(result, NestedPolicies)
        assert (result.wider_label, result.lost) == (engine.label, 0)


def test_two_policies_that_are_not_nested_still_get_the_real_paired_test() -> None:
    """The refusal must be narrow. Two *different* signals cut at one coverage deliver
    overlapping-but-neither-nested sets, each side can gain, and that is a genuine null --
    it is the ``a=17, b=3, p=0.0026`` comparison the findings page keeps.

    Four turns: each policy delivers two, sharing one. So each side gains exactly one useful
    answer the other lacks, both discordant cells are non-zero, and the delta is 0.
    """
    arm = _arm(
        [
            _row("a", correct=True, usage=_tokens(10)),
            _row("b", correct=True, usage=_tokens(20)),
            _row("c", correct=True, usage=_tokens(30)),
            _row("d", correct=False, usage=_tokens(40)),
        ]
    )
    left = DeliveryPolicy(label="left", population=arm, delivered=frozenset({"a", "b"}))
    right = DeliveryPolicy(label="right", population=arm, delivered=frozenset({"b", "c"}))

    result = compare_policies(left, right)
    assert isinstance(result, McNemarResult), "a crossing pair is a real comparison"
    assert (result.both, result.only_a, result.only_b, result.neither) == (1, 1, 1, 1)
    assert result.delta.value == pytest.approx(0.0)


def test_a_policy_compared_against_itself_is_not_a_null_result() -> None:
    """Equal delivery sets are one policy, and p = 1.0 over zero discordant pairs reads as
    evidence of no difference rather than as the tautology it is -- ``open-work.md`` §3.9's
    "assert a constant equals itself" with a statistic attached."""
    arm = _arm([_row("a", correct=True, usage=_tokens(10))])
    engine = engine_policy(arm)

    result = compare_policies(engine, engine)
    assert isinstance(result, NestedPolicies)
    assert (result.lost, result.withheld) == (0, 0)


def test_a_comparison_across_two_different_populations_is_refused() -> None:
    """Two arms are not one paired comparison, and ``mcnemar`` is where that is caught.

    This is what stops the sub-population curves the driver prints separately from being
    differenced against the main table.
    """
    left = _arm([_row("a", correct=True, usage=_tokens(10))])
    right = _arm([_row("z", correct=True, usage=_tokens(10))])
    with pytest.raises(ValueError, match="unit sets differ"):
        compare_policies(engine_policy(left), engine_policy(right))


def test_a_policy_cannot_deliver_a_turn_the_population_does_not_contain() -> None:
    arm = _arm([_row("a", correct=True, usage=_tokens(10))])
    with pytest.raises(ValueError, match="absent from"):
        DeliveryPolicy(label="invented", population=arm, delivered=frozenset({"a", "ghost"}))


# ── operating-point questions ─────────────────────────────────────────────────


def test_coverage_at_a_target_accuracy_ignores_a_lucky_prefix() -> None:
    """52 turns: the first two are right, the rest are 50/50.

    ``k = 2`` reaches accuracy 1.0 and is not an operating point -- two answers is not a
    policy. With ``min_delivered = 2`` the same curve happily reports it, which is what
    makes this assertion able to fail.
    """
    rows = [_row("r1", correct=True, usage=_tokens(1)), _row("r2", correct=True, usage=_tokens(2))]
    rows += [
        _row(f"x{i}", correct=bool(i % 2), usage=_tokens(10 + i)) for i in range(50)
    ]
    curve = risk_coverage(_arm(rows), SIGNALS["agent_out_tok"])
    assert curve.coverage_for(0.95, min_delivered=2).delivered == 2
    assert curve.coverage_for(0.95).why_absent
    assert "no k >= 50" in curve.coverage_for(0.95).why_absent


def test_coverage_at_a_target_accuracy_is_the_largest_k_and_not_the_first() -> None:
    """Ranked T, F, T, T: accuracy runs 1.0, 0.5, 0.667, 0.75.

    Target 0.7 is cleared at k = 1 and again at k = 4. The first is a two-answer product;
    the second is the operating point a buyer is asking about. Reporting the first gives
    coverage 0.2 where the honest answer is 0.8.
    """
    curve = risk_coverage(
        _arm(
            [
                _row("a", correct=True, usage=_tokens(10)),
                _row("b", correct=False, usage=_tokens(20)),
                _row("c", correct=True, usage=_tokens(30)),
                _row("d", correct=True, usage=_tokens(40)),
                _row("e", outcome="refused", correct=False),
            ]
        ),
        SIGNALS["agent_out_tok"],
    )
    point = curve.coverage_for(0.7, min_delivered=1)
    assert point.delivered == 4
    assert point.coverage.value == pytest.approx(0.8)
    assert point.accuracy.value == pytest.approx(0.75)


def test_a_policy_carries_its_arms_filter_trail_so_mismatched_pairs_are_refused() -> None:
    """Two populations over the same turns but filtered differently are not one pair.

    ``as_population`` rebuilds rows from scratch, and a rebuild that forgot to carry
    ``filtered_by`` would let the driver's sub-population curves -- the ones printed
    under "signals present on only part of the arm" -- be differenced against the main
    table. ``mcnemar`` is where that has to be caught, and it can only catch it if the
    trail survives the rebuild.
    """
    arm = _arm([_row("a", correct=True, usage=_tokens(10)), _row("b", correct=False, usage=_tokens(20))])
    narrower = arm.restrict(lambda _r: True, "delivered turns carrying some signal")
    assert engine_policy(narrower).as_population().filtered_by == narrower.filtered_by
    with pytest.raises(ValueError, match="filtered differently"):
        compare_policies(engine_policy(arm), engine_policy(narrower))


def test_every_curve_meets_the_engine_at_the_engines_own_coverage() -> None:
    """The structural claim the findings page rests on.

    A ranking reorders the delivered turns; it cannot un-withhold a declined one. So at
    coverage = delivered/n every signal, the oracle included, reads the same accuracy.
    The assertion can fail: rank the declines above the delivered turns and the oracle
    reads 0.0 here instead of 0.5.
    """
    arm = _arm(
        [
            _row("a", correct=True, usage=_tokens(10)),
            _row("b", correct=False, usage=_tokens(20)),
            _row("c", outcome="refused", correct=False, computed_correct=False),
            _row("d", outcome="capped", correct=False, computed_correct=False),
        ]
    )
    at = engine_policy(arm).point().coverage.value
    assert at == pytest.approx(0.5)
    for curve in (oracle(arm), no_ranking(arm), risk_coverage(arm, SIGNALS["agent_out_tok"])):
        assert curve.accuracy_at(at).accuracy.value == pytest.approx(0.5), curve.signal


# ── the driver is wired ───────────────────────────────────────────────────────

#: Repository root, from ``tests/measure/`` upward.
ROOT = Path(__file__).resolve().parent.parent.parent


def test_the_driver_runs_end_to_end_on_a_synthetic_artifact(tmp_path: Path) -> None:
    """``tools/selective_curve.py`` against a hand-built arm, as a subprocess.

    Its real inputs live under ``runs/``, which is gitignored and absent on CI, so
    without this the driver is a ``tools/`` script nothing ever executes -- the
    "declared machinery with no wire" shape ``open-work.md`` §3.10 says is this
    repository's recurring defect. Naming it ``check_*`` would instead subject it to
    ``tests/conformance/test_register_closure.py``'s CI-or-manual declaration, and it
    reports rather than gates.

    The asserted numbers are hand-computed: 4 delivered turns of which 3 are right, one
    decline, so coverage is 4/5 and selective accuracy 3/4. Both appear on the operating
    point line, and the coincidence line must print exactly one distinct accuracy.
    """
    artifact = tmp_path / "synthetic_arm.jsonl"
    rows = [
        _row("a", correct=True, usage=_tokens(10), licensed=["t"], attempts=[{"passed": True}]),
        _row("b", correct=True, usage=_tokens(20), licensed=["t"], attempts=[{"passed": True}]),
        _row("c", correct=True, usage=_tokens(30), licensed=["t"], attempts=[{"passed": True}]),
        _row("d", correct=False, usage=_tokens(40), licensed=["t"], attempts=[{"passed": False}]),
        _row("e", outcome="refused", correct=False, computed_correct=False,
             usage=[], licensed=[], attempts=[{"passed": False}]),
    ]
    artifact.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "selective_curve.py"), str(artifact)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "coverage 0.8000 (4/5)  accuracy 0.7500" in result.stdout
    assert "every curve reads: ['0.7500']" in result.stdout
    # The decline is priced and would have been wrong, so the rate is 1.0 over n=1 and
    # the denominator travels with it -- on the rate's own rendering, not only the object's.
    assert "declines that would have been wrong: 1.0000 (1/1)" in result.stdout
    assert "n=1" in result.stdout and "0 of 1 decline(s)" in result.stdout
    # Every curve cell carries the k it was read at. Without it, `0.7500` at a requested
    # coverage of 0.7 and `1.0000` at the realisable policy below it are two numbers on one
    # page with no way to tell which k each belongs to -- which is how the v4 findings page
    # attached the k=944 policy's consequence to the k=945 curve cell.
    assert "0.7500@3" in result.stdout, "the curve cell must name its k"
    assert "coverage 0.6000 (3/5)" in result.stdout, "the policy must name its realised coverage"
    # And the nested trade is arithmetic, not a hypothesis test.
    assert "No paired test is reported" in result.stdout
    assert "p=" not in result.stdout, "no p-value can be printed for a nested comparison"
