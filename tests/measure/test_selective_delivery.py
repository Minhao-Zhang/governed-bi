"""Assertions are hand-computed literals. Ties are averaged, and an absent signal is not zero.

The grade never reaches a signal. A nested comparison is not a test, and a figure names the k it was read at.
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
from governed_bi.registry.quantity import NotMeasured


def _row(qid: str, *, outcome: str = "answered", correct: bool | None = True, **extra: object):
    row: dict[str, object] = {"question_id": qid, "outcome": outcome, "correct": correct}
    row.update(extra)
    return row


def _tokens(n: int) -> list[dict[str, object]]:
    return [{"stage": "agent_core", "output_tokens": n, "input_tokens": 10, "model_calls": 1}]


def _config(rows: list[dict[str, object]]) -> Population:
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
    """Artifact order inside a tie would make a constant signal look like it separates."""
    config = _config(_ALL_TIED)
    curve = risk_coverage(config, SIGNALS["agent_out_tok"])
    assert curve.errors == (0.5, 1.0, 1.5, 2.0, 3.0)
    assert curve.aurc.value == pytest.approx(0.52)
    assert curve.aurc.value == pytest.approx(no_ranking(config).aurc.value)
    # And the AUC agrees: one distinct value cannot discriminate, so mid-ranks put it at
    # exactly 0.5. Without mid-ranks the same fixture scores 0.25 or 0.75 depending on
    # which of the two right answers the driver wrote first.
    assert curve.auc.value == pytest.approx(0.5)


def test_a_tie_group_is_one_operating_point_and_a_policy_cannot_split_it() -> None:
    """A cut that splits a tie delivers nothing. Accuracy of that empty policy is unmeasured, not zero."""
    curve = risk_coverage(_config(_ALL_TIED), SIGNALS["agent_out_tok"])
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
        _config(
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
    """Flipping the grades must make the curve worse. A sign fitted per config cannot lose."""
    inverted = risk_coverage(
        _config(
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
        _config(
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
    """An absent ledger read as zero attempts looks like a constant signal."""
    ledger = SIGNALS["n_attempts"]
    assert ledger.read({"attempts": []}) == 0.0
    assert ledger.read({"attempts": [{"passed": True}]}) == 1.0
    assert ledger.read({}) is None
    assert SIGNALS["n_failed_attempts"].read({}) is None


def test_a_turn_that_is_neither_answered_nor_a_declared_abstention_is_refused() -> None:
    """A third kind of ending has to be classified before coverage means anything."""
    config = Population.of("odd", [_row("a", outcome="mystery", correct=False)])
    with pytest.raises(ValueError, match="neither an answer nor a declared abstention"):
        engine_policy(config)


def test_crashes_and_ungraded_turns_leave_the_population_with_their_filters_named() -> None:
    """A crash must not be counted as an abstention: it is not a decision.

    ``correct is None`` leaves too -- ``docs/measurement.md`` says a grade the harness
    could not make is not a wrong answer.
    """
    config = graded(
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
    assert config.units == {"a", "d"}
    assert config.filtered_by == (
        "excluded crashed turns",
        "excluded turns the grader could not judge",
    )
    assert engine_policy(config).point().coverage.value == pytest.approx(0.5)


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
    """A denylist only catches a field the probe row carries. The allowlist catches the name anyway."""
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
        _config(
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
        _config(
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
    """No attribute is the bare rate. ``share`` demands the denominator back."""
    wrong = PricedAbstention.of(
        _config(
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
        _config(
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
    config = _config(
        [
            _row("a", outcome="refused", correct=False, computed_correct=False),
            _row("b", correct=True, usage=_tokens(10)),
        ]
    )
    declined = config.restrict(lambda r: r.get("outcome") == "refused", "declined turns only")
    with pytest.raises(ValueError, match="not a restriction"):
        PricedAbstention(declined=declined, priced=config)


def test_a_priced_abstention_never_credits_the_engine_for_a_withheld_answer() -> None:
    """A decline that would have been right stays wrong on the operating point."""
    config = _config(
        [
            _row("a", correct=True, usage=_tokens(10)),
            _row("b", correct=False, usage=_tokens(20)),
            _row("c", outcome="capped", correct=False, computed_correct=True),
        ]
    )
    point = engine_policy(config).point()
    assert (point.delivered, point.correct) == (2, 1.0)
    assert point.accuracy.value == pytest.approx(0.5)
    wrong = PricedAbstention.of(config).would_have_been_wrong
    assert (wrong.wrong, wrong.priced) == (0, 1)
    assert wrong.share(1).value == pytest.approx(0.0)


# ── comparisons go through the one McNemar ────────────────────────────────────


def test_a_nested_trade_is_reported_as_arithmetic_and_not_as_a_test() -> None:
    """One delivery set contains the other, so a p-value would restate the subset. The finding is the count lost."""
    config = _config(
        [
            _row("a", correct=True, usage=_tokens(10)),
            _row("b", correct=True, usage=_tokens(20)),
            _row("c", correct=True, usage=_tokens(30)),
            _row("d", correct=False, usage=_tokens(40)),
        ]
    )
    engine = engine_policy(config)
    ranked = risk_coverage(config, SIGNALS["agent_out_tok"]).policy_at_most(0.5)
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
    config = _config(
        [
            _row("a", correct=True, usage=_tokens(10)),
            _row("b", correct=False, usage=_tokens(20)),
        ]
    )
    engine = engine_policy(config)
    narrower = DeliveryPolicy(label="narrower", population=config, delivered=frozenset({"a"}))

    for pair in ((engine, narrower), (narrower, engine)):
        result = compare_policies(*pair)
        assert isinstance(result, NestedPolicies)
        assert (result.wider_label, result.lost) == (engine.label, 0)


def test_two_policies_that_are_not_nested_still_get_the_real_paired_test() -> None:
    """Crossing delivery sets still get a paired test. The refusal is only for a subset."""
    config = _config(
        [
            _row("a", correct=True, usage=_tokens(10)),
            _row("b", correct=True, usage=_tokens(20)),
            _row("c", correct=True, usage=_tokens(30)),
            _row("d", correct=False, usage=_tokens(40)),
        ]
    )
    left = DeliveryPolicy(label="left", population=config, delivered=frozenset({"a", "b"}))
    right = DeliveryPolicy(label="right", population=config, delivered=frozenset({"b", "c"}))

    result = compare_policies(left, right)
    assert isinstance(result, McNemarResult), "a crossing pair is a real comparison"
    assert (result.both, result.only_a, result.only_b, result.neither) == (1, 1, 1, 1)
    assert result.delta.value == pytest.approx(0.0)


def test_a_policy_compared_against_itself_is_not_a_null_result() -> None:
    """The same delivery set against itself is a tautology, not evidence of no difference."""
    config = _config([_row("a", correct=True, usage=_tokens(10))])
    engine = engine_policy(config)

    result = compare_policies(engine, engine)
    assert isinstance(result, NestedPolicies)
    assert (result.lost, result.withheld) == (0, 0)


def test_a_comparison_across_two_different_populations_is_refused() -> None:
    """Two configs are not one paired comparison."""
    left = _config([_row("a", correct=True, usage=_tokens(10))])
    right = _config([_row("z", correct=True, usage=_tokens(10))])
    with pytest.raises(ValueError, match="unit sets differ"):
        compare_policies(engine_policy(left), engine_policy(right))


def test_a_policy_cannot_deliver_a_turn_the_population_does_not_contain() -> None:
    config = _config([_row("a", correct=True, usage=_tokens(10))])
    with pytest.raises(ValueError, match="absent from"):
        DeliveryPolicy(label="invented", population=config, delivered=frozenset({"a", "ghost"}))


# ── operating-point questions ─────────────────────────────────────────────────


def test_coverage_at_a_target_accuracy_ignores_a_lucky_prefix() -> None:
    """Two perfect answers at the top are not an operating point."""
    rows = [_row("r1", correct=True, usage=_tokens(1)), _row("r2", correct=True, usage=_tokens(2))]
    rows += [
        _row(f"x{i}", correct=bool(i % 2), usage=_tokens(10 + i)) for i in range(50)
    ]
    curve = risk_coverage(_config(rows), SIGNALS["agent_out_tok"])
    assert curve.coverage_for(0.95, min_delivered=2).delivered == 2
    assert curve.coverage_for(0.95).why_absent
    assert "no k >= 50" in curve.coverage_for(0.95).why_absent


def test_coverage_at_a_target_accuracy_is_the_largest_k_and_not_the_first() -> None:
    """The target is cleared twice. The answer is the largest k, not the first."""
    curve = risk_coverage(
        _config(
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


def test_a_policy_carries_its_configs_filter_trail_so_mismatched_pairs_are_refused() -> None:
    """``as_population`` must keep ``filtered_by``, or two filters over the same turns look like one pair."""
    config = _config([_row("a", correct=True, usage=_tokens(10)), _row("b", correct=False, usage=_tokens(20))])
    narrower = config.restrict(lambda _r: True, "delivered turns carrying some signal")
    assert engine_policy(narrower).as_population().filtered_by == narrower.filtered_by
    with pytest.raises(ValueError, match="filtered differently"):
        compare_policies(engine_policy(config), engine_policy(narrower))


def test_every_curve_meets_the_engine_at_the_engines_own_coverage() -> None:
    """A ranking cannot un-withhold a decline, so every curve meets the engine at the engine's coverage."""
    config = _config(
        [
            _row("a", correct=True, usage=_tokens(10)),
            _row("b", correct=False, usage=_tokens(20)),
            _row("c", outcome="refused", correct=False, computed_correct=False),
            _row("d", outcome="capped", correct=False, computed_correct=False),
        ]
    )
    at = engine_policy(config).point().coverage.value
    assert at == pytest.approx(0.5)
    for curve in (oracle(config), no_ranking(config), risk_coverage(config, SIGNALS["agent_out_tok"])):
        assert curve.accuracy_at(at).accuracy.value == pytest.approx(0.5), curve.signal


# ── the driver is wired ───────────────────────────────────────────────────────

#: Repository root, from ``tests/measure/`` upward.
ROOT = Path(__file__).resolve().parent.parent.parent


def test_the_driver_runs_end_to_end_on_a_synthetic_artifact(tmp_path: Path) -> None:
    """The driver has no committed inputs, so this runs it on a synthetic file."""
    artifact = tmp_path / "synthetic_config.jsonl"
    rows = [
        _row("a", correct=True, usage=_tokens(10), retrieved_tables=["t"], attempts=[{"passed": True}]),
        _row("b", correct=True, usage=_tokens(20), retrieved_tables=["t"], attempts=[{"passed": True}]),
        _row("c", correct=True, usage=_tokens(30), retrieved_tables=["t"], attempts=[{"passed": True}]),
        _row("d", correct=False, usage=_tokens(40), retrieved_tables=["t"], attempts=[{"passed": False}]),
        _row("e", outcome="refused", correct=False, computed_correct=False,
             usage=[], retrieved_tables=[], attempts=[{"passed": False}]),
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
    # Each curve cell names its k. A requested coverage and the policy below it are different k.
    assert "0.7500@3" in result.stdout, "the curve cell must name its k"
    assert "coverage 0.6000 (3/5)" in result.stdout, "the policy must name its realised coverage"
    # And the nested trade is arithmetic, not a hypothesis test.
    assert "No paired test is reported" in result.stdout
    assert "p=" not in result.stdout, "no p-value can be printed for a nested comparison"
