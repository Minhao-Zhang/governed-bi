"""``arm_power_refusal``: the caller ``eval/power.py`` did not have.

``open-work.md`` §3.10 recorded ``require_power`` as a gate nothing invoked. A gate with no caller is
a preference, and the specific cost of this one having none is on record: the run it was taken from
measured a treatment reaching 9 of 131 questions against an MDE of 9.6pp and reported the null as a
finding about the feature.

**The arithmetic is not re-derived here.** ``tests/eval/test_an_underpowered_arm_refuses_to_be_
declared.py`` pins ``require_power`` itself. What this file pins is the *wiring*: that a profile's
declaration reaches the gate, that the two ways of declaring nothing behave differently, and that
the message says the discordance is an estimate.
"""

from __future__ import annotations

from governed_bi.eval.provenance import PRIOR_DISCORDANT, PRIOR_OF, arm_power_refusal
from governed_bi.register.arm_profiles import ArmProfile


def _profile(**over: object) -> ArmProfile:
    base: dict[str, object] = dict(
        name="v6",
        description="a corpus release",
        treatment=frozenset({"corpus_release"}),
        corpus_content_hash="c" * 64,
        question_subset="1351:423a3f4b65fb",
    )
    base.update(over)
    return ArmProfile(**base)  # type: ignore[arg-type]


def test_an_arm_that_cannot_detect_its_hypothesis_is_refused() -> None:
    """1pp on 131 questions. The floor at the prior discordance is 9.6pp, so this arm would spend
    its whole budget to report a null about its sample."""
    refusal = arm_power_refusal(_profile(hypothesised_effect=0.01, readout="EX"), 131)
    assert refusal is not None
    assert "cannot detect its own hypothesis on EX" in refusal
    assert "0.0956" in refusal


def test_a_detectable_hypothesis_passes() -> None:
    assert arm_power_refusal(_profile(hypothesised_effect=0.05, readout="EX"), 1351) is None


def test_an_arm_that_declares_no_hypothesis_is_silent_rather_than_refused() -> None:
    """Every arm on disk predates the field. Inventing an effect size so the gate has something to
    check would put this module's number into a later quotation of the arm's."""
    assert arm_power_refusal(_profile(), 1351) is None


def test_an_effect_with_no_readout_is_refused() -> None:
    """MDE is in points of the whole population and two readouts' base rates differ by two orders
    of magnitude, so an effect size with no quantity attached cannot be compared to a floor. A
    draft of this design read a mechanism indicator's smaller MDE as the better instrument; it was
    a unit error, and naming the readout is what makes it visible."""
    refusal = arm_power_refusal(_profile(hypothesised_effect=0.05), 1351)
    assert refusal is not None
    assert "no readout" in refusal
    assert "two orders of magnitude" in refusal


def test_the_refusal_says_the_discordance_is_an_estimate() -> None:
    """The number is carried from another repository's paired run. Presenting it as a measurement of
    *this* arm is the mistake ``eval/power.py``'s docstring exists to head off, and the refusal is
    where a reader meets it."""
    refusal = arm_power_refusal(_profile(hypothesised_effect=0.01, readout="EX"), 131)
    assert refusal is not None
    assert "ESTIMATE" in refusal
    assert "another repository" in refusal


def test_the_prior_is_the_one_pair_on_disk_and_not_a_round_number() -> None:
    """20 of 131, kept as two integers. A decimal `0.153` reads like a rate somebody measured on
    this tree; the fraction reads like what it is, which is the distinction `eval/power.py`'s
    docstring exists for. `check_measurement_locality.py` refuses the decimal outright."""
    assert (PRIOR_DISCORDANT, PRIOR_OF) == (20, 131)


def test_a_tiny_arm_still_gets_at_least_one_discordant_pair() -> None:
    """``require_power`` refuses zero discordance as unmeasurable rather than as infinite
    precision, so an arm small enough to round the estimate to zero must not read as a pass."""
    refusal = arm_power_refusal(_profile(hypothesised_effect=0.5, readout="EX"), 3)
    assert refusal is not None, "n=3 cannot detect a 50pp effect and must not pass"
