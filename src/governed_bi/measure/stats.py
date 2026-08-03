"""One McNemar, one MDE, one rule-of-three. Each with exactly one home.

v1 had **two McNemars**, two EX definitions, and two ``LOW_CONFIDENCE_JOIN``
constants *with different comparison operators*. That is why ADR 0005 §6 says one
implementation per concept, one import name, and why
``tools/check_one_implementation.py`` names :func:`mcnemar` as a declared singleton.
With this rewrite being parcelled out to work in parallel, a second McNemar is the
default outcome rather than a slip.

**The test is exact, not approximate**, and that is a design decision with a
reason. A normal approximation needs a continuity-correction choice, and a choice is
a place two implementations can differ while both look right — which is precisely
how v1 ended up with two. Under the null, each discordant pair is an independent
coin flip, so the exact two-sided binomial is available from :mod:`math` alone: no
dependency, no tuning knob, nothing to diverge on.

**The MDE is post-hoc and says so.** It is computed from the *observed* discordance,
because the standard error of a paired difference depends on how often the two arms
disagree, not on ``n`` alone. An MDE derived from ``n`` and a base rate — which is
what gets quoted when discordance is not to hand — is a different and generally
smaller number, and quoting it makes an underpowered comparison look decisive. So
:func:`mde` **requires** the discordance and returns unmeasured without it.

**A zero count is bounded, not measured.** :func:`rule_of_three` returns a
:class:`~governed_bi.register.quantity.Relation.at_most` bound, so "we observed no
failures in 200 trials" renders as ``<= 1.50%`` and cannot be quoted as 0%. v1
published the zero.
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

    The four cell counts are here because the p-value alone cannot be audited: a
    p-value with 6 discordant pairs and one with 600 look identical in a table and
    mean entirely different things. ADR 0005 §4.1's rule that a rate must be
    published with its count is the same principle.
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
        """Whether the observed delta clears the comparison's own detection floor.

        Not a significance test. A delta smaller than the MDE may still have a small
        p-value and is not something this design will act on — the retired v1 numbers
        include several sub-MDE deltas reported as findings.
        """
        if not (self.delta.is_measured and self.minimum_detectable.is_measured):
            return False
        return abs(self.delta.value) >= self.minimum_detectable.value


def mcnemar(a: Population, b: Population, outcome: str) -> McNemarResult:
    """Exact paired comparison of ``outcome`` between two populations.

    Refuses, rather than adapting, when the two populations are not the same
    population differently treated:

    * different ``filtered_by`` trails — the L-R3 defect, caught structurally
    * different ``unit_key`` — the pairing would be meaningless
    * different unit sets — silently intersecting them is how v1 compared 1351
      questions against 1025 and reported the delta as if both arms had answered
      everything

    The last one is the important refusal. Intersecting is *almost always* what the
    caller wants, which is why it must be done explicitly: an arm that crashed on 300
    questions and an arm that did not are not comparable on the 1051 that survived
    without saying so, because the 300 are not missing at random.
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
    ``P = 2 * P(X <= min(b, c))`` for ``X ~ Binomial(b + c, 1/2)``, capped at 1.

    With zero discordant pairs the formula yields 1.0 without a special case, which
    is the right answer and worth not special-casing: the informativeness of that 1.0
    is carried by the MDE, not by pretending the p-value is absent.
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

    Each of these is a place where "just make it work" produces a plausible number
    from an invalid comparison, and every one of them has a v1 instance.
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
