"""Whether an arm can detect the effect it hypothesises, asked before it runs.

Experiment 008 measured a treatment that reached 9 of 131 questions, against an MDE of
9.6pp, and reported the resulting null as a finding about the feature. It was a finding
about the sample. This module makes that arithmetic a precondition of declaring an arm.

The formula is **paired McNemar**, not the two-independent-proportions normal
approximation:

    MDE = (z_alpha + z_beta) * sqrt(p_discordant / n)   where p_discordant = discordant / n

not

    MDE = (z_alpha + z_beta) * sqrt(2 * p * (1 - p) / n)

The two arms being compared run the *same* questions, so the comparison is paired, and the
pairing is most of the power. The two-independent-proportions formula gives 0.1571 at 008's
own inputs (n=131, baseline_rate=0.290) -- 1.6x more conservative than the 0.0956 008's
SUMMARY actually reported for its McNemar test -- and a gate built on it would refuse arms
that are in fact adequately powered. That is the mirror image of the defect this module
exists to prevent, and worse for carrying a gate's authority while being wrong.

**``discordant`` is an estimate, not a measurement, when used here.** It is the count of
paired questions where the two arms disagreed, and that count is only known once a
comparison has actually run -- so a pre-check has nothing but a comparable prior run to
estimate it from. The only prior available is experiment 008's ``beer_factory`` pair: 20
discordant of 131 questions (15.3%). Presenting the MDE this produces as an exact figure,
rather than as an estimate carried forward from that one prior run, is the mistake this
docstring exists to head off.
"""

from __future__ import annotations

import math

__all__ = ["UnderpoweredArm", "minimum_detectable_effect", "require_power"]

#: Two-sided 0.05 and 80% power, the conventional pair. Stated as constants because an arm
#: that quietly relaxed them would pass this gate and still be undetectable.
_Z_ALPHA = 1.959963984540054
_Z_POWER = 0.8416212335729143


class UnderpoweredArm(ValueError):
    """An arm hypothesising an effect smaller than it could detect."""


def minimum_detectable_effect(
    n: int, discordant: int, *, alpha: float = 0.05, power: float = 0.80
) -> float:
    """Smallest absolute effect this ``n``, at this discordant count, could distinguish from
    zero under paired McNemar.

    ``discordant`` is the number of paired questions where the two arms disagreed -- not a
    population size and not a baseline accuracy. See the module docstring: before a
    comparison has run, this is necessarily an estimate carried forward from a comparable
    prior run.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 < discordant < n:
        raise ValueError("discordant must lie strictly between 0 and n")
    if (alpha, power) != (0.05, 0.80):
        raise NotImplementedError(
            "only alpha=0.05 / power=0.80 are calibrated here; adding another pair means "
            "computing its z-scores, not interpolating"
        )
    p_discordant = discordant / n
    return (_Z_ALPHA + _Z_POWER) * math.sqrt(p_discordant / n)


def require_power(n: int, discordant: int, hypothesised_effect: float) -> None:
    """Raise :class:`UnderpoweredArm` unless ``n`` at ``discordant`` could detect
    ``hypothesised_effect``. ``discordant`` is an estimate -- see the module docstring."""
    mde = minimum_detectable_effect(n, discordant)
    if abs(hypothesised_effect) < mde:
        raise UnderpoweredArm(
            f"this arm hypothesises an effect of {hypothesised_effect} but n={n} with an "
            f"estimated {discordant} of {n} discordant pairs can only detect {mde} as a "
            "minimum. Raise n at a comparable discordance rate, or restrict the population "
            "to the stratum the treatment actually reaches."
        )
