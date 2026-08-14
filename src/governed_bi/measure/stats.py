"""One McNemar, one MDE, one rule-of-three (ADR 0005 §6 singleton).

Exact two-sided binomial McNemar. MDE is post-hoc from observed discordance.
:func:`rule_of_three` returns an at-most bound, not a measured zero.
"""


from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

from ..register.quantity import Measured, Relation
from .population import Population

__all__ = ["McNemarResult", "mcnemar", "mde", "rule_of_three"]


@dataclass(frozen=True)
class McNemarResult:
    """A paired comparison, with everything needed to judge whether it is decisive.

    The four cell counts are here because a p-value alone cannot be audited: one over 6
    discordant pairs and one over 600 look identical in a table (ADR 0005 §4.1).
    """

    a_label: str
    b_label: str
    n_pairs: int
    both: int
    only_a: int
    only_b: int
    neither: int
    delta: Measured[float]
    discordance: Measured[float]
    p_value: Measured[float]
    minimum_detectable: Measured[float]

    def render(self) -> str:
        """One auditable line. Formatting goes through ``Measured.render`` only."""
        return (
            f"{self.b_label} - {self.a_label}: {self.delta.render(4)} "
            f"(p={self.p_value.render(4)}, n={self.n_pairs}, "
            f"discordant={self.only_a + self.only_b}, "
            f"MDE={self.minimum_detectable.render(4)})"
        )

    @property
    def is_decisive(self) -> bool:
        """Whether the observed delta clears this comparison's own MDE (not a p-value test)."""
        if not (self.delta.is_measured and self.minimum_detectable.is_measured):
            return False
        return abs(self.delta.value) >= self.minimum_detectable.value


def mcnemar(a: Population, b: Population, outcome: str) -> McNemarResult:
    """Exact paired comparison of ``outcome`` between two populations.

    Refuses on different ``filtered_by``, ``unit_key``, or unit sets; intersect
    explicitly if that is the intended population.
    """
    if a.filtered_by != b.filtered_by:
        raise ValueError(
            f"populations were filtered differently: {a.describe()} vs {b.describe()}. "
            "A headline and a test over different row sets is L-R3; restrict both "
            "the same way, or state the asymmetry and construct them explicitly."
        )
    if a.unit_key != b.unit_key:
        raise ValueError(f"unit keys differ: {a.unit_key!r} vs {b.unit_key!r}")
    if a.units != b.units:
        only_a, only_b = len(a.units - b.units), len(b.units - a.units)
        raise ValueError(
            f"unit sets differ: {only_a} only in {a.label!r}, {only_b} only in "
            f"{b.label!r}. Intersect deliberately with .restrict() and label it — "
            "units missing from one arm are not missing at random."
        )

    rows_a, rows_b = a.by_unit(), b.by_unit()
    incomplete = [u for u in a.units if rows_a[u].get(outcome) is None or rows_b[u].get(outcome) is None]
    n_pairs = len(a.units)

    if incomplete:
        absent = Measured.unmeasured(
            f"{len(incomplete)}/{n_pairs} pairs lack {outcome!r} on one or both sides; "
            "an absent outcome is not a negative one"
        )
        return McNemarResult(
            a_label=a.label, b_label=b.label, n_pairs=n_pairs,
            both=0, only_a=0, only_b=0, neither=0,
            delta=absent, discordance=absent, p_value=absent, minimum_detectable=absent,
        )

    both = only_a = only_b = neither = 0
    for unit in a.units:
        ya, yb = bool(rows_a[unit][outcome]), bool(rows_b[unit][outcome])
        if ya and yb:
            both += 1
        elif ya:
            only_a += 1
        elif yb:
            only_b += 1
        else:
            neither += 1

    discordance = Measured.rate(only_a + only_b, n_pairs, what=f"discordance on {outcome!r}")
    delta = Measured.rate(only_b - only_a, n_pairs, what=f"delta in {outcome!r} rate")
    return McNemarResult(
        a_label=a.label,
        b_label=b.label,
        n_pairs=n_pairs,
        both=both,
        only_a=only_a,
        only_b=only_b,
        neither=neither,
        delta=delta,
        discordance=discordance,
        p_value=_exact_two_sided(only_a, only_b),
        minimum_detectable=mde(n_pairs, discordance),
    )


def _exact_two_sided(only_a: int, only_b: int) -> Measured[float]:
    """Two-sided exact binomial p over the discordant pairs.

    Under the null each discordant pair favours either arm with probability 1/2, so
    ``P = 2 * P(X <= min(b, c))`` for ``X ~ Binomial(b + c, 1/2)``, capped at 1. Zero
    discordant pairs yields 1.0 and is not special-cased: the MDE carries how
    informative that 1.0 is.
    """
    n = only_a + only_b
    if n == 0:
        return Measured.of(1.0)
    k = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return Measured.of(min(1.0, 2.0 * tail))


def mde(
    n_pairs: int,
    discordance: Measured[float],
    *,
    alpha: float = 0.05,
    power: float = 0.80,
) -> Measured[float]:
    """Smallest paired difference this comparison could have detected.

    ``(z_{1-alpha/2} + z_{power}) * sqrt(d / n)``, where ``d`` is the discordance
    rate. **Requires** ``d``: the standard error of a paired difference depends on how
    often the arms disagree, and an MDE computed from ``n`` and a base rate alone is a
    smaller, different number that makes an underpowered comparison look decisive.

    Post-hoc by construction, since ``d`` is observed. State it as such wherever it
    is reported; it answers "could this comparison have seen the effect" and not
    "how many questions should the next run use".
    """
    if n_pairs <= 0:
        return Measured.unmeasured("no pairs, so no detection floor")
    if not discordance.is_measured:
        return Measured.unmeasured(
            f"MDE needs the observed discordance: {discordance.why}"
        )
    d = discordance.value
    if d <= 0:
        return Measured.unmeasured(
            "zero discordant pairs: the arms never disagreed, so this comparison "
            "has no resolution to report. Widen n or accept that nothing is "
            "detectable here."
        )
    normal = NormalDist()
    z = normal.inv_cdf(1.0 - alpha / 2.0) + normal.inv_cdf(power)
    return Measured.of(z * math.sqrt(d / n_pairs))


def rule_of_three(n_trials: int) -> Measured[float]:
    """Upper bound on a rate after observing zero events in ``n_trials``.

    ``3/n`` at roughly 95% confidence. Returned as an
    :attr:`~governed_bi.register.quantity.Relation.at_most` bound, so it renders with
    its inequality attached and cannot be quoted as a point estimate — which is what
    v1 did when it published a refusal rate of 0% from a gate that had never fired.
    """
    if n_trials <= 0:
        return Measured.unmeasured("no trials, so no bound")
    return Measured.of(3.0 / n_trials).bounded(Relation.at_most)


def _assert_the_guards_fire() -> None:
    """Import-time checks on the three refusals most likely to be relaxed.

    Each is a place where relaxing the refusal produces a plausible number from an
    invalid comparison.
    """
    a = Population.of("a", [{"question_id": "1", "ok": True}])
    b = Population.of("b", [{"question_id": "2", "ok": True}])
    try:
        mcnemar(a, b, "ok")
    except ValueError:
        pass
    else:  # pragma: no cover - import-time guard
        raise AssertionError("mcnemar compared disjoint unit sets without refusing")

    if rule_of_three(200).relation is not Relation.at_most:  # pragma: no cover
        raise AssertionError("rule_of_three returned a point estimate, not a bound")

    if mde(100, Measured.unmeasured("probe")).is_measured:  # pragma: no cover
        raise AssertionError("mde produced a number without the discordance")


_assert_the_guards_fire()
