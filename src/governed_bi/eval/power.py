"""Whether a config can detect the effect it hypothesises, before it runs.

The MDE formula lives only in ``measure.stats.mde`` (ADR 0005). A second copy
disagreed in the last digit, so this module calls it and does not restate it.
``discordant`` here is an estimate from a prior run, not a measurement of this
config.
"""

from __future__ import annotations

from governed_bi.measure.stats import mde
from governed_bi.registry.quantity import Measured

__all__ = ["UnderpoweredConfiguration", "require_power"]


class UnderpoweredConfiguration(ValueError):
    """The hypothesised effect sits under the detection floor."""


def require_power(n: int, discordant: int, hypothesised_effect: float) -> None:
    """A float ``discordant`` is a rate and understated the floor. An unmeasured MDE refuses rather than passes."""
    if not isinstance(discordant, int):
        raise TypeError(
            f"discordant must be a count of disagreeing pairs, not {discordant!r}. A float "
            "here is the discordance *rate*, and a rate passed as a count understates the "
            "detection floor by about sqrt(n) -- which passes a config that cannot detect its "
            "own hypothesis"
        )
    floor = mde(n, Measured.rate(discordant, n, what=f"discordance over {n} pairs"))
    if not floor.is_measured:
        raise UnderpoweredConfiguration(
            f"n={n} with an estimated {discordant} discordant pairs has no detection floor to "
            f"compare {hypothesised_effect} against: {floor.why}"
        )
    if abs(hypothesised_effect) < floor.value:
        raise UnderpoweredConfiguration(
            f"this config hypothesises an effect of {hypothesised_effect} but n={n} with an "
            f"estimated {discordant} of {n} discordant pairs can only detect "
            f"{floor.render(4)} as a minimum. Raise n at a comparable discordance rate, or "
            "restrict the population to the stratum the treatment actually reaches."
        )
