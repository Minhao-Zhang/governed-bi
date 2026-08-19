"""Whether an arm can detect the effect it hypothesises, asked before it runs.

Taken from RyanChenJung/governed-bi-utkuai@12c3e15. The prior it is calibrated against is
**their** paired run, named as such throughout: this repository has published no arm carrying
these figures, and a number whose provenance has been rubbed out is a number the next reader
quotes as ours.

Their run measured a treatment that reached 9 of 131 questions, against an MDE of 9.6pp, and
reported the resulting null as a finding about the feature. It was a finding about the sample.
This module makes that arithmetic a precondition of declaring an arm.

**The arithmetic is not here.** :func:`governed_bi.measure.stats.mde` is the one MDE in this
repository -- a declared singleton (ADR 0005 §6, ``tools/check_one_implementation.py``) -- and
this module calls it. It used to restate the same paired-McNemar formula under the name
``minimum_detectable_effect``, with its own hardcoded z-constants, and the two copies already
disagreed in the last digit at that run's own inputs. A synonym is how that survived: the
singleton gate is keyed on the name ``mde``, so a second implementation spelled differently sat
beside a declared singleton and no rule looked at it -- which is the observation `20d3df8` added
the singleton entry for. ``eval/report.py`` reaches down into ``measure/`` for ``mcnemar`` the
same way.

What is left here is the **gate**, which is a different thing from the formula: it converts a
discordant *count* into the discordance rate ``mde`` requires, and refuses an arm whose
hypothesis sits under the floor.

``stats.mde``'s own docstring says why that rate is a required argument, and what an MDE
computed from ``n`` and a base rate alone is instead. Read it before quoting a number from here;
the warning governs every caller and is deliberately not restated.

**``discordant`` is an estimate, not a measurement, when used here.** It is the count of paired
questions where the two arms disagreed, and that count is only known once a comparison has
actually run -- so a pre-check has nothing but a comparable prior run to estimate it from. The
only prior available is the fork's ``beer_factory`` pair: 20 discordant of 131 questions (15.3%),
which is the 0.0956 this gate is calibrated against. Presenting the MDE this produces as an exact
figure, rather than as an estimate carried forward from one prior run in another tree, is the
mistake this docstring exists to head off.
"""

from __future__ import annotations

from governed_bi.measure.stats import mde
from governed_bi.register.quantity import Measured

__all__ = ["UnderpoweredArm", "require_power"]


class UnderpoweredArm(ValueError):
    """An arm hypothesising an effect smaller than it could detect."""


def require_power(n: int, discordant: int, hypothesised_effect: float) -> None:
    """Raise :class:`UnderpoweredArm` unless ``n`` at ``discordant`` could detect
    ``hypothesised_effect``. ``discordant`` is an estimate -- see the module docstring.

    ``discordant`` is a **count** of disagreeing pairs and must be an ``int``. A float is
    refused rather than converted, because the confusion it invites is the one failure this
    gate cannot afford: ``require_power(n=131, discordant=0.29, hypothesised_effect=0.03)``
    passed silently on an MDE of 0.0115 -- one eighth of the true 0.0956 -- so the gate
    approved the exact arm shape it exists to refuse. Delegating the formula does not catch
    that on its own: ``stats.mde`` takes a *rate*, and 0.29 is a well-formed rate.

    An MDE that cannot be computed is not a pass. ``stats.mde`` returns an unmeasured quantity
    for no pairs and for zero discordance, and both of those mean this arm has no resolution to
    spend, so both refuse here and carry ``mde``'s own reason.
    """
    if not isinstance(discordant, int):
        raise TypeError(
            f"discordant must be a count of disagreeing pairs, not {discordant!r}. A float "
            "here is the discordance *rate*, and a rate passed as a count understates the "
            "detection floor by about sqrt(n) -- which passes an arm that cannot detect its "
            "own hypothesis"
        )
    floor = mde(n, Measured.rate(discordant, n, what=f"discordance over {n} pairs"))
    if not floor.is_measured:
        raise UnderpoweredArm(
            f"n={n} with an estimated {discordant} discordant pairs has no detection floor to "
            f"compare {hypothesised_effect} against: {floor.why}"
        )
    if abs(hypothesised_effect) < floor.value:
        raise UnderpoweredArm(
            f"this arm hypothesises an effect of {hypothesised_effect} but n={n} with an "
            f"estimated {discordant} of {n} discordant pairs can only detect "
            f"{floor.render(4)} as a minimum. Raise n at a comparable discordance rate, or "
            "restrict the population to the stratum the treatment actually reaches."
        )
