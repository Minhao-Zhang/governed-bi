"""Experiment 008 spent a full two-arm run to learn its MDE was 9.6pp -- afterwards.

The treatment moved a population of 9 questions out of 131. No effect that population could
produce was detectable, and the run reported "no change" as though that were a measurement of
the feature rather than of the sample size. This refuses that run up front.

``eval/power.py`` holds the gate and not the formula: the MDE is
:func:`governed_bi.measure.stats.mde`, the declared singleton, and ``power.py`` used to restate
it under a synonym with hardcoded z-constants. These tests therefore anchor the *singleton*
against 008's published figure and assert that the gate built on it still refuses 008's arm --
the two claims that a delegation could break independently.
"""

from __future__ import annotations

import pytest

from governed_bi.eval.power import UnderpoweredArm, require_power
from governed_bi.measure.stats import mde
from governed_bi.register.quantity import Measured


def _discordance(discordant: int, n: int) -> Measured[float]:
    """008's inputs in the shape ``stats.mde`` takes: a rate, not a count."""
    return Measured.rate(discordant, n, what=f"discordance over {n} pairs")


def test_the_one_mde_reproduces_the_figure_experiment_008_reported() -> None:
    """008's SUMMARY reports ``McNemar: -0.0153 (p=0.8238, n=131, discordant=20,
    MDE=0.0956)``. Anchoring on that published number is how we know the function is
    calibrated against a real run and not merely self-consistent, and it is the only external
    check this arithmetic has -- so it anchors ``measure.stats.mde`` itself, not a wrapper that
    could drift away from it."""
    floor = mde(131, _discordance(20, 131))

    assert floor.render(4) == "0.0956"
    assert floor.value == pytest.approx(0.0956, abs=0.001)


def test_more_questions_lower_the_bar() -> None:
    """008's beer_factory pair discorded on 20 of 131 (15.3%). Four times the n at the same
    discordance rate should detect a materially smaller effect -- more data buys power,
    it does not just rescale the same answer."""
    assert mde(524, _discordance(80, 524)).value < mde(131, _discordance(20, 131)).value


def test_an_arm_hypothesising_less_than_its_mde_is_refused() -> None:
    """008's shape: a treatment reaching 9 of 131 questions cannot produce 9.6pp even if it
    fixes every one of them."""
    with pytest.raises(UnderpoweredArm, match="9.6pp|0.09"):
        require_power(n=131, discordant=20, hypothesised_effect=0.03)


def test_an_adequately_powered_arm_passes_silently() -> None:
    require_power(n=131, discordant=20, hypothesised_effect=0.15)


def test_a_discordance_rate_passed_as_a_count_is_refused() -> None:
    """The gate approving the arm it exists to refuse. ``discordant`` is a count and ``PLAN.md``
    documented it as ``baseline_rate``, so ``require_power(n=131, discordant=0.29,
    hypothesised_effect=0.03)`` passed silently against an MDE of 0.0115 -- one eighth of the
    true 0.0956. Delegating to ``measure.stats.mde`` does not catch it, because that function
    takes a rate and 0.29 is a well-formed one, so an explicit refusal is what prevents it.

    All three parameters are positional, so the positional spelling is pinned too."""
    with pytest.raises(TypeError, match="count"):
        require_power(n=131, discordant=0.29, hypothesised_effect=0.03)
    with pytest.raises(TypeError, match="count"):
        require_power(131, 0.29, 0.03)


def test_an_arm_with_no_pairs_is_refused_rather_than_passed() -> None:
    """A gate that accepted n=0 would silently declare every arm powered enough to detect
    anything, since there would be no run to be underpowered against. ``stats.mde`` answers
    "not measured" here rather than raising, so the gate has to read an absent floor as a
    refusal -- reading it as a pass is the direction that costs a run."""
    with pytest.raises(UnderpoweredArm, match="no detection floor"):
        require_power(n=0, discordant=0, hypothesised_effect=0.03)


def test_an_arm_whose_prior_never_disagreed_is_refused() -> None:
    """Zero discordant pairs is not a measurement -- McNemar has nothing to test -- and
    ``stats.mde`` says so as an unmeasured quantity rather than as a number. The gate must
    refuse on it: two arms that never disagree have no resolution to spend."""
    with pytest.raises(UnderpoweredArm, match="no detection floor"):
        require_power(n=131, discordant=0, hypothesised_effect=0.03)
