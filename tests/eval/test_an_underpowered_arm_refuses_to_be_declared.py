"""Experiment 008 spent a full two-arm run to learn its MDE was 9.6pp -- afterwards.

The treatment moved a population of 9 questions out of 131. No effect that population could
produce was detectable, and the run reported "no change" as though that were a measurement of
the feature rather than of the sample size. This refuses that run up front.

The formula here is the *paired* McNemar MDE, not the two-independent-proportions one: the
two arms run the same questions, so the comparison is paired. A two-independent-proportions
gate would report 0.1571 at 008's own inputs (n=131, baseline 0.290) -- 1.6x too conservative
against the 0.0956 008 actually published -- and would refuse arms that are in fact adequately
powered. That is the mirror image of the defect this module exists to prevent.
"""

from __future__ import annotations

import pytest

from governed_bi.eval.power import UnderpoweredArm, minimum_detectable_effect, require_power


def test_it_reproduces_the_mde_experiment_008_reported() -> None:
    """008's SUMMARY reports ``McNemar: -0.0153 (p=0.8238, n=131, discordant=20,
    MDE=0.0956)``. Anchoring on that published number is how we know this function is
    calibrated against a real run and not merely self-consistent."""
    mde = minimum_detectable_effect(n=131, discordant=20)

    assert mde == pytest.approx(0.0956, abs=0.001)


def test_more_questions_lower_the_bar() -> None:
    """008's beer_factory pair discorded on 20 of 131 (15.3%). Four times the n at the same
    discordance rate should detect a materially smaller effect -- more data buys power,
    it does not just rescale the same answer."""
    assert minimum_detectable_effect(n=524, discordant=80) < minimum_detectable_effect(
        n=131, discordant=20
    )


def test_an_arm_hypothesising_less_than_its_mde_is_refused() -> None:
    """008's shape: a treatment reaching 9 of 131 questions cannot produce 9.6pp even if it
    fixes every one of them."""
    with pytest.raises(UnderpoweredArm, match="9.6pp|0.09"):
        require_power(n=131, discordant=20, hypothesised_effect=0.03)


def test_an_adequately_powered_arm_passes_silently() -> None:
    require_power(n=131, discordant=20, hypothesised_effect=0.15)


def test_n_must_be_positive() -> None:
    """A gate that accepted n=0 would silently declare every arm powered enough to detect
    anything, since there would be no run to be underpowered against."""
    with pytest.raises(ValueError, match="positive"):
        minimum_detectable_effect(n=0, discordant=0)


def test_discordant_must_lie_strictly_between_zero_and_n() -> None:
    """Zero discordant pairs is not a measurement -- McNemar has nothing to test -- and more
    discordant pairs than questions is not a valid count at all."""
    with pytest.raises(ValueError, match="discordant"):
        minimum_detectable_effect(n=131, discordant=0)
    with pytest.raises(ValueError, match="discordant"):
        minimum_detectable_effect(n=131, discordant=131)


def test_only_the_calibrated_alpha_and_power_are_accepted() -> None:
    """The two z-scores are baked in as constants (see the module docstring). Accepting a
    different (alpha, power) pair without computing its z-scores would silently relax the
    gate this module exists to keep strict."""
    with pytest.raises(NotImplementedError):
        minimum_detectable_effect(n=131, discordant=20, alpha=0.10, power=0.80)
