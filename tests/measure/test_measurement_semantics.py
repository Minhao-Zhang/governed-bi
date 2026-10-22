"""Assertions are effects against hand-computed literals, not a module compared with its own constant.

The property throughout is that an absent value is not zero.
"""

from __future__ import annotations

import math

import pytest

from governed_bi.measure import checks, stats
from governed_bi.measure.population import Population
from governed_bi.registry.quantity import Measured, NotMeasured, Relation, State
from governed_bi.registry.record import CHECK_CONDITIONS

# ── the value type: absence must be unusable as a number ──────────────────────


def test_a_measured_has_no_truth_value() -> None:
    """``if rate:`` is False for a measured 0.0 and False for no measurement.

    Those are opposite conclusions, so the expression must not compile to a silent
    choice between them. Same reasoning as ``settings.UNSET``.
    """
    with pytest.raises(TypeError):
        bool(Measured.of(0.0))
    with pytest.raises(TypeError):
        bool(Measured.unmeasured("probe"))


def test_arithmetic_on_a_measured_is_a_type_error() -> None:
    """No operators, so a measurement cannot be coerced into a total by accident.

    v1 summed a cost table with a missing model and published the sum of the models
    it happened to know.
    """
    m = Measured.of(1.0)
    for other in (1, 1.0, Measured.of(1.0)):
        with pytest.raises(TypeError):
            m + other  # type: ignore[operator]


def test_reaching_for_an_absent_value_raises_rather_than_returning_none() -> None:
    with pytest.raises(NotMeasured):
        Measured.unmeasured("no price row").value


def test_non_finite_and_none_are_rejected_at_construction() -> None:
    """``nan`` reaching a report is L-R1 wearing a different string.

    Worse than ``0``, in fact: ``0`` looks like a claim, ``nan`` looks like a bug the
    reader will assume someone noticed.
    """
    for bad in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError):
            Measured.of(bad)
    with pytest.raises(ValueError):
        Measured.of(None)  # type: ignore[arg-type]


def test_an_absence_must_carry_a_reason() -> None:
    for factory in (Measured.unmeasured, Measured.inapplicable):
        with pytest.raises(ValueError):
            factory("")


def test_a_rate_over_zero_is_not_zero() -> None:
    """The denominator rule, and the reason ADR 0005 §4.1 requires the count.

    v1's quotability check read a degradation rate of "0 over 0 turns" as a pass on
    runs where the fan-out never ran.
    """
    assert not Measured.rate(0, 0, what="probe").is_measured
    assert Measured.rate(1, 4, what="probe").value == 0.25


def test_an_absent_quantity_never_renders_as_a_number() -> None:
    """The last function to touch a value is where v1 turned it into ``0.0``."""
    for m in (Measured.unmeasured("endpoint died"), Measured.inapplicable("no lexical channel")):
        rendered = m.render(2, "%")
        assert not any(c.isdigit() for c in rendered), rendered
        assert "0" not in rendered


def test_a_bound_cannot_render_as_a_point_estimate() -> None:
    """Observing 0 events in 200 trials does not measure a rate of 0."""
    rendered = Measured.of(0.015).bounded(Relation.at_most).render(2, "%", scale=100)
    assert rendered == "<= 1.50%"


def test_absence_and_boundedness_both_survive_map() -> None:
    """A monotone transform of a bound is still a bound; forgetting that loses it."""
    absent = Measured.unmeasured("probe").map(lambda v: v * 2)
    assert absent.state is State.not_measured and absent.why == "probe"
    bound = Measured.of(0.5).bounded(Relation.at_least).map(lambda v: v * 2)
    assert bound.relation is Relation.at_least and bound.value == 1.0


def test_combine_is_unmeasured_if_either_side_is() -> None:
    total = Measured.of(3.0).combine(
        Measured.unmeasured("no output token count"), lambda a, b: a + b, what="cost"
    )
    assert not total.is_measured
    assert "output token count" in total.why


# ── the population: one row set per metric ────────────────────────────────────


def test_duplicate_units_are_refused_at_construction() -> None:
    """v1 merged 1025 rows and 326 rows into one config score, double-weighting."""
    with pytest.raises(ValueError, match="duplicated"):
        Population.of("a", [{"question_id": "1"}, {"question_id": "1"}])


def test_a_row_with_no_unit_id_is_refused() -> None:
    with pytest.raises(ValueError, match="question_id"):
        Population.of("a", [{"correct": True}])


def test_an_absent_outcome_is_not_a_failed_outcome() -> None:
    """The single most-repeated v1 defect, at the point it entered every rate.

    An config whose instrumentation dropped ``correct`` on some rows has an *unknown*
    score. v1 reported it as a low one, i.e. as a bad config rather than a broken one.
    """
    p = Population.of("a", [{"question_id": "1", "correct": True}, {"question_id": "2"}])
    assert not p.count("correct").is_measured
    assert not p.rate("correct").is_measured
    assert p.coverage("correct").value == 0.5


def test_a_filter_must_be_labelled() -> None:
    p = Population.of("a", [{"question_id": "1"}])
    with pytest.raises(ValueError):
        p.restrict(lambda r: True, "")


def test_restrict_records_its_trail_so_two_populations_can_be_compared() -> None:
    p = Population.of("a", [{"question_id": str(i)} for i in range(4)])
    q = p.restrict(lambda r: r["question_id"] != "0", "excluded crashes")
    assert q.filtered_by == ("excluded crashes",)
    assert q.n == 3 and p.n == 4, "restrict must not mutate its source"


# ── the statistics: one implementation, and it refuses invalid comparisons ────


def test_mcnemar_refuses_populations_filtered_differently() -> None:
    """L-R3, caught structurally rather than by review.

    v1 computed a headline over one row set and its test over another, and neither
    call site could see the other's filter.
    """
    rows = [{"question_id": str(i), "ok": True} for i in range(4)]
    a = Population.of("a", rows)
    b = Population.of("b", rows).restrict(lambda r: True, "excluded crashes")
    with pytest.raises(ValueError, match="filtered differently"):
        stats.mcnemar(a, b, "ok")


def test_mcnemar_refuses_to_silently_intersect_unit_sets() -> None:
    """Intersecting has to be explicit: the missing units are not missing at random."""
    a = Population.of("a", [{"question_id": str(i), "ok": True} for i in range(5)])
    b = Population.of("b", [{"question_id": str(i), "ok": True} for i in range(4)])
    with pytest.raises(ValueError, match="unit sets differ"):
        stats.mcnemar(a, b, "ok")


def test_mcnemar_p_value_against_a_hand_computed_literal() -> None:
    """The p-value is a literal computed outside the module, so the test does not reimplement the code."""
    rows_a, rows_b = [], []
    for i in range(2):  # only a correct
        rows_a.append({"question_id": f"a{i}", "ok": True})
        rows_b.append({"question_id": f"a{i}", "ok": False})
    for i in range(8):  # only b correct
        rows_a.append({"question_id": f"b{i}", "ok": False})
        rows_b.append({"question_id": f"b{i}", "ok": True})
    for i in range(10):  # concordant, must not enter the p-value
        rows_a.append({"question_id": f"c{i}", "ok": True})
        rows_b.append({"question_id": f"c{i}", "ok": True})

    result = stats.mcnemar(Population.of("a", rows_a), Population.of("b", rows_b), "ok")
    assert (result.only_a, result.only_b, result.both, result.neither) == (2, 8, 10, 0)
    assert result.p_value.value == pytest.approx(0.109375, abs=1e-12)
    # 20 pairs: 2 + 8 discordant, 10 concordant. delta = (8 - 2)/20, discordance = 10/20.
    assert result.n_pairs == 20
    assert result.delta.value == pytest.approx(0.30)
    assert result.discordance.value == pytest.approx(0.50)


def test_mcnemar_is_unmeasured_when_an_outcome_is_missing_on_either_side() -> None:
    a = Population.of("a", [{"question_id": "1", "ok": True}])
    b = Population.of("b", [{"question_id": "1"}])
    result = stats.mcnemar(a, b, "ok")
    assert not result.p_value.is_measured and not result.delta.is_measured


def test_mde_requires_the_observed_discordance() -> None:
    """An MDE from ``n`` alone is a smaller, different number.

    Quoting it makes an underpowered comparison look decisive, and this project has
    already published sub-MDE deltas as findings.
    """
    assert not stats.mde(1351, Measured.unmeasured("not computed")).is_measured
    assert not stats.mde(0, Measured.of(0.3)).is_measured
    assert not stats.mde(1351, Measured.of(0.0)).is_measured


def test_mde_matches_the_closed_form_at_a_known_point() -> None:
    """``(1.959964 + 0.841621) * sqrt(0.30/1351)``, computed outside the module."""
    got = stats.mde(1351, Measured.of(0.30))
    assert got.value == pytest.approx(2.8015843 * math.sqrt(0.30 / 1351), rel=1e-5)


def test_a_delta_smaller_than_the_mde_is_not_decisive() -> None:
    rows_a, rows_b = [], []
    for i in range(1000):
        same = i % 2 == 0
        rows_a.append({"question_id": str(i), "ok": same})
        rows_b.append({"question_id": str(i), "ok": same if i != 7 else not same})
    result = stats.mcnemar(Population.of("a", rows_a), Population.of("b", rows_b), "ok")
    assert not result.is_decisive


def test_rule_of_three_is_a_bound_and_never_zero() -> None:
    assert stats.rule_of_three(200).relation is Relation.at_most
    assert stats.rule_of_three(200).value == pytest.approx(0.015)
    assert not stats.rule_of_three(0).is_measured


# ── the checks: a check that did not happen is not a check that passed ─────────


def test_every_declared_check_has_an_implementation() -> None:
    """Cross-module closure. v1 shipped eight declared-but-dead checks.

    Legitimate as a comparison of two modules' constants — unlike asserting one
    module against its own, which passes for an empty table.
    """
    assert set(checks.CHECK_IMPLEMENTATIONS) == set(CHECK_CONDITIONS)


def test_an_uninstrumented_config_is_not_quotable() -> None:
    """An absent counter is not a pass."""
    config = Population.of("bare", [{"question_id": str(i), "correct": True} for i in range(50)])
    ok, results = checks.quotable(config)
    assert not ok
    assert {r.result for r in results} == {checks.Result.cannot_evaluate}
    assert len(results) == len(CHECK_CONDITIONS)


def test_a_crash_fails_the_outcome_check() -> None:
    """A crash counted as a refusal contaminated every v1 config-to-config delta."""
    rows = [{"question_id": str(i), "crashed": i == 3} for i in range(20)]
    result = checks.CHECK_IMPLEMENTATIONS["outcome"](Population.of("config", rows))
    assert result.result is checks.Result.failed
    assert result.observed.value == pytest.approx(1 / 20)


def test_a_fully_instrumented_clean_config_passes_the_zero_count_checks() -> None:
    """The complement. Without it, "fails everything" would also satisfy the test
    above, and a check that always fails gets switched off."""
    rows = [{"question_id": str(i), "crashed": False} for i in range(20)]
    result = checks.CHECK_IMPLEMENTATIONS["outcome"](Population.of("config", rows))
    assert result.result is checks.Result.passed
    assert result.observed.value == 0.0


def test_zero_fanout_turns_cannot_evaluate_the_degradation_check() -> None:
    """An empty ``channel_scoring`` is not "no channel differed"."""
    refused = [{"question_id": str(i), "channel_scoring": None} for i in range(30)]
    result = checks.CHECK_IMPLEMENTATIONS["channel_scoring"](Population.of("refusals", refused))
    assert result.result is checks.Result.cannot_evaluate
    assert "0 over 0" in result.detail


def test_the_degradation_check_runs_on_the_turns_that_did_fan_out() -> None:
    rows = [
        {"question_id": str(i), "channel_scoring": {"schema": "ran"}, "channel_degraded": i == 2}
        for i in range(10)
    ] + [{"question_id": f"r{i}", "channel_scoring": None} for i in range(5)]
    result = checks.CHECK_IMPLEMENTATIONS["channel_scoring"](Population.of("mixed", rows))
    assert result.result is checks.Result.failed
    assert "n=10" in result.population, "the denominator must exclude the refusals"


def test_a_fully_instrumented_config_can_be_quotable() -> None:
    """A complete ``context_hash`` must be a pass, or this can never return true."""
    rows = [
        {
            "question_id": str(i),
            "correct": i % 2 == 0,
            "outcome": "answered",
            # The `outcome` check reads a `crashed` boolean, not the outcome string, and refuses
            # to evaluate when it is absent on any row -- correctly, since an absent counter is
            # not a zero. Present here so the *other* checks are what this test is about.
            "crashed": False,
            "context_hash": f"hash-{i}",
            "guardrail_error": False,
            "re_served": False,
            "negative_failed_open": False,
            "channel_degraded": False,
            "channel_scoring": {"channel_schema": {"lexical": "ran"}},
            # Present so this fixture is fully instrumented. Absence is not a zero.
            "settings_resolved": {"route_top_n": 3, "candidate_depth": 50},
            # One value: one config is one corpus. The cross-config half is not this check.
            "corpus_content_hash": "corpus-30872d3",
        }
        for i in range(50)
    ]
    ok, results = checks.quotable(Population.of("instrumented", rows))
    assert ok, {r.field: (r.result.value, r.detail) for r in results}
    assert {r.result for r in results} == {checks.Result.passed}


def test_one_config_two_configurations_is_not_one_config() -> None:
    """The failure names the differing settings, not only the count."""
    from governed_bi.registry.settings import setting_names

    def row(i: int, top_n: int) -> dict:
        return {
            "question_id": str(i),
            "correct": True,
            "outcome": "answered",
            "crashed": False,
            "context_hash": f"hash-{i}",
            "guardrail_error": False,
            "re_served": False,
            "negative_failed_open": False,
            "channel_degraded": False,
            "channel_scoring": {"channel_schema": {"lexical": "ran"}},
            "settings_resolved": {"route_top_n": top_n, "candidate_depth": 50},
        }

    assert "route_top_n" in setting_names(), (
        "the setting this fixture drifts is no longer a resume-drift key, so the test proves nothing"
    )

    drifted = checks.CHECK_IMPLEMENTATIONS["settings_resolved"](
        Population.of("drifted", [row(i, 3 if i < 25 else 5) for i in range(50)])
    )
    assert drifted.result is checks.Result.failed
    assert "route_top_n" in drifted.detail, drifted.detail
    assert "candidate_depth" not in drifted.detail, "only the settings that differ are named"

    steady = checks.CHECK_IMPLEMENTATIONS["settings_resolved"](
        Population.of("steady", [row(i, 3) for i in range(50)])
    )
    assert steady.result is checks.Result.passed

    # Absent is not passing. `Absence.never` already reports these as missing_required, and this
    # check reporting clean on the same row would be two checks disagreeing about one hole.
    missing = checks.CHECK_IMPLEMENTATIONS["settings_resolved"](
        Population.of("missing", [{"question_id": "a", "correct": True}])
    )
    assert missing.result is checks.Result.cannot_evaluate

    # A type change is a configuration change: 3 and "3" are two configurations, and a
    # comparison that coerced them would report drift as agreement.
    typed = checks.CHECK_IMPLEMENTATIONS["settings_resolved"](
        Population.of(
            "typed",
            [
                {**row(i, 3), "settings_resolved": {"route_top_n": 3 if i < 25 else "3"}}
                for i in range(50)
            ],
        )
    )
    assert typed.result is checks.Result.failed


def test_partial_context_hash_coverage_fails_rather_than_abstaining() -> None:
    """Recorded on only some turns is a defect, not an absence."""
    rows = [
        {
            "question_id": str(i),
            "correct": True,
            "outcome": "answered",
            "crashed": False,
            "context_hash": None if i == 0 else f"hash-{i}",
            "guardrail_error": False,
            "re_served": False,
            "negative_failed_open": False,
            "channel_degraded": False,
        }
        for i in range(50)
    ]
    ok, results = checks.quotable(Population.of("partial", rows))
    assert not ok
    ctx = next(r for r in results if r.field == "context_hash")
    assert ctx.result is checks.Result.failed, ctx.detail
