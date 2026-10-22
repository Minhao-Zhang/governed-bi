"""A plane of curves, not a confidence model. Ties are averaged, never split by row order.

Two policies are compared only through ``mcnemar``. Declines are priced in ``abstention``, where the denominator is
different.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable

from ..registry.quantity import Measured
from ..registry.stages import Outcome
from .population import Population, TurnRow
from .signals import Direction, Signal
from .stats import McNemarResult, mcnemar

__all__ = [
    "DECLINED",
    "MIN_OPERATING_POINT",
    "OperatingPoint",
    "DeliveryPolicy",
    "NestedPolicies",
    "RiskCoverage",
    "graded",
    "engine_policy",
    "risk_coverage",
    "oracle",
    "no_ranking",
    "compare_policies",
]

# Below this, the largest k that clears a high accuracy target is a run of luck
# at the top of the ranking, not a policy anyone can operate.
MIN_OPERATING_POINT = 50

# ``crashed`` is absent: a crash is a bug, not a decision. ``no_sql`` is included:
# a turn with no statement did not get a governed answer, and dropping it hides that.
# ``clarification`` covers a pause and a decline; ``_split`` rejects any other ending.
DECLINED: frozenset[str] = frozenset(
    {
        Outcome.refused.value,
        Outcome.capped.value,
        Outcome.clarification.value,
        Outcome.no_sql.value,
    }
)


def graded(config: Population) -> Population:
    """One function, so the headline and the comparison share a row set.

    Crashed turns are dropped: a crash is not a decision to decline. ``correct is None`` is dropped too: it is not
    wrong.
    """
    return config.restrict(
        lambda r: r.get("outcome") != Outcome.crashed.value, "excluded crashed turns"
    ).restrict(
        lambda r: r.get("correct") is not None, "excluded turns the grader could not judge"
    )


def _split(config: Population) -> tuple[tuple[TurnRow, ...], tuple[TurnRow, ...]]:
    delivered = tuple(r for r in config.rows if r.get("outcome") == Outcome.answered.value)
    declined = tuple(r for r in config.rows if r.get("outcome") in DECLINED)
    if len(delivered) + len(declined) != config.n:
        unknown = sorted(
            {
                str(r.get("outcome"))
                for r in config.rows
                if r.get("outcome") != Outcome.answered.value and r.get("outcome") not in DECLINED
            }
        )
        raise ValueError(
            f"{config.describe()} contains outcome(s) that are neither an answer nor a declared "
            f"abstention: {unknown}. Coverage is the share of turns that got an answer, so a "
            "third kind of ending has to be classified before it can be counted -- putting it "
            "silently on either side moves the headline."
        )
    return delivered, declined


@dataclass(frozen=True)
class OperatingPoint:
    """``correct`` is a float: inside a tie the curve reports the expectation under uniform tie-breaking."""

    label: str
    n: int
    delivered: int
    correct: float
    population: str
    why_absent: str = ""

    @property
    def coverage(self) -> Measured[float]:
        if self.why_absent:
            return Measured.unmeasured(self.why_absent)
        return Measured.rate(self.delivered, self.n, what=f"coverage at {self.label!r}")

    @property
    def accuracy(self) -> Measured[float]:
        if self.why_absent:
            return Measured.unmeasured(self.why_absent)
        return Measured.rate(
            self.correct, self.delivered, what=f"selective accuracy at {self.label!r}"
        )

    def render(self) -> str:
        if self.why_absent:
            return f"{self.label:34s} no operating point: {self.why_absent}"
        return (
            f"{self.label:34s} coverage {self.coverage.render(4)} ({self.delivered}/{self.n})"
            f"  accuracy {self.accuracy.render(4)}"
        )


@dataclass(frozen=True)
class DeliveryPolicy:
    """A set of turns, not a threshold. A threshold on a tied signal does not name the turns."""

    label: str
    population: Population
    delivered: frozenset[str]

    def __post_init__(self) -> None:
        stray = self.delivered - self.population.units
        if stray:
            raise ValueError(
                f"policy {self.label!r} delivers {len(stray)} turn(s) absent from "
                f"{self.population.describe()}, e.g. {sorted(stray)[:3]}. A policy over units "
                "the population does not contain cannot be paired against another policy."
            )

    @property
    def useful(self) -> int:
        by_unit = self.population.by_unit()
        return sum(1 for u in self.delivered if bool(by_unit[u].get("correct")))

    def point(self) -> OperatingPoint:
        """A policy that delivers nothing still has coverage zero. Its accuracy is unmeasured, not a rate of zero."""
        return OperatingPoint(
            label=self.label,
            n=self.population.n,
            delivered=len(self.delivered),
            correct=float(self.useful),
            population=self.population.describe(),
        )

    def as_population(self) -> Population:
        """``filtered_by`` is carried over, so two differently filtered configs are refused by ``mcnemar``."""
        rows = [
            {
                "question_id": unit,
                "useful_answer": unit in self.delivered and bool(row.get("correct")),
            }
            for unit, row in self.population.by_unit().items()
        ]
        built = Population.of(f"{self.population.label} under {self.label}", rows)
        return replace(built, filtered_by=self.population.filtered_by)


def engine_policy(config: Population) -> DeliveryPolicy:
    delivered, _ = _split(config)
    return DeliveryPolicy(
        label="engine (governance + attempt cap)",
        population=config,
        delivered=frozenset(str(r[config.unit_key]) for r in delivered),
    )


@dataclass(frozen=True)
class RiskCoverage:
    """Declined turns sit below every score: no ranking can un-withhold one, so every gain costs coverage."""

    signal: str
    direction: Direction
    population: Population
    ranked: tuple[tuple[str, float, bool], ...]
    errors: tuple[float, ...]
    n_declined: int
    unavailable: str = ""

    @property
    def n(self) -> int:
        return self.population.n

    @property
    def aurc(self) -> Measured[float]:
        """Averaged over ``k``, not integrated over coverage, so the number does not depend on an interpolation. An AURC
        alone has no scale."""
        if self.unavailable:
            return Measured.unmeasured(self.unavailable)
        return Measured.rate(
            sum(e / (k + 1) for k, e in enumerate(self.errors)),
            len(self.errors),
            what=f"AURC of {self.signal!r}",
        )

    @property
    def auc(self) -> Measured[float]:
        """Left in the raw score direction. Re-signing it would turn a wrong claim into a weak curve."""
        if self.unavailable:
            return Measured.unmeasured(self.unavailable)
        return _rank_auc([(score, not wrong) for _, score, wrong in self.ranked])

    def accuracy_at(self, coverage: float) -> OperatingPoint:
        if self.unavailable:
            return self._absent(f"cov {coverage}", self.unavailable)
        k = math.floor(coverage * self.n)
        if k < 1 or k > self.n:
            return self._absent(
                f"cov {coverage}", f"{coverage} is not a reachable share of {self.n} turns"
            )
        return self._point(f"{self.signal} @ cov {coverage}", k)

    def coverage_for(
        self, accuracy: float, *, min_delivered: int = MIN_OPERATING_POINT
    ) -> OperatingPoint:
        """Largest ``k``, not the first. The first ``k`` to clear the bar is usually a handful of turns at the top."""
        if self.unavailable:
            return self._absent(f"acc {accuracy}", self.unavailable)
        best = 0
        for k in range(min_delivered, self.n + 1):
            if (k - self.errors[k - 1]) / k >= accuracy:
                best = k
        if not best:
            return self._absent(
                f"acc {accuracy}",
                f"no k >= {min_delivered} reaches {accuracy}, and a handful of turns at the "
                "top of the ranking is a fluke rather than an operating point",
            )
        return self._point(f"{self.signal} @ acc {accuracy}", best)

    def policy_at_most(self, coverage: float, *, label: str = "") -> DeliveryPolicy:
        """The cut falls between distinct scores. A set that splits a tie is not a policy this signal can express, so
        the cut moves down."""
        limit = math.floor(coverage * self.n)
        cut = 0
        for index in range(1, min(limit, len(self.ranked)) + 1):
            if index == len(self.ranked) or self.ranked[index][1] != self.ranked[index - 1][1]:
                cut = index
        return DeliveryPolicy(
            # Presentation only. This method does not round; formatting belongs to Measured.render.
            label=label or f"{self.signal} <= cov {coverage}",
            population=self.population,
            delivered=frozenset(unit for unit, _, _ in self.ranked[:cut]),
        )

    def realisable_coverages(self) -> tuple[float, ...]:
        """One entry per tie boundary. An AURC averaged through ties describes points no policy can stand on."""
        if self.unavailable:
            return ()
        out: list[float] = []
        for index in range(1, len(self.ranked) + 1):
            if index == len(self.ranked) or self.ranked[index][1] != self.ranked[index - 1][1]:
                out.append(index / self.n)
        return tuple(out)

    def _point(self, label: str, k: int) -> OperatingPoint:
        return OperatingPoint(
            label=label,
            n=self.n,
            delivered=k,
            correct=k - self.errors[k - 1],
            population=self.population.describe(),
        )

    def _absent(self, label: str, why: str) -> OperatingPoint:
        return OperatingPoint(
            label=f"{self.signal} @ {label}",
            n=self.n,
            delivered=0,
            correct=0.0,
            population=self.population.describe(),
            why_absent=why,
        )


def _rank_auc(scored: list[tuple[float, bool]]) -> Measured[float]:
    order = sorted(scored, key=lambda pair: pair[0])
    ranks = [0.0] * len(order)
    start = 0
    while start < len(order):
        stop = start
        while stop + 1 < len(order) and order[stop + 1][0] == order[start][0]:
            stop += 1
        mid = (start + stop) / 2.0 + 1.0
        for index in range(start, stop + 1):
            ranks[index] = mid
        start = stop + 1
    positives = sum(1 for _, label in order if label)
    negatives = len(order) - positives
    if not positives or not negatives:
        return Measured.unmeasured(
            "AUC needs both a right and a wrong answer in the population; this one has "
            f"{positives} right and {negatives} wrong"
        )
    total = sum(ranks[i] for i, (_, label) in enumerate(order) if label)
    return Measured.of((total - positives * (positives + 1) / 2.0) / (positives * negatives))


def _curve_from_scores(
    config: Population, name: str, direction: Direction, score: Callable[[TurnRow], float | None]
) -> RiskCoverage:
    delivered, declined = _split(config)
    scores = [score(row) for row in delivered]
    blind = sum(1 for value in scores if value is None)
    if blind:
        return RiskCoverage(
            signal=name, direction=direction, population=config, ranked=(), errors=(),
            n_declined=len(declined),
            unavailable=(
                f"{blind} of {len(delivered)} delivered turns carry no {name!r}, so the ranking "
                "would be over a sub-population and its AURC would not be comparable to any "
                "other signal's on this config"
            ),
        )
    sign = 1.0 if direction is Direction.lower_first else -1.0
    scored = [
        (str(row[config.unit_key]), float(value), not bool(row.get("correct")))
        for value, row in zip(scores, delivered)
        if value is not None
    ]
    # Stable sort. Nothing downstream may depend on artifact order inside a tie.
    ranked = tuple(sorted(scored, key=lambda entry: sign * entry[1]))

    errors: list[float] = []
    cumulative = 0.0
    start = 0
    while start < len(ranked):
        stop = start
        while stop + 1 < len(ranked) and ranked[stop + 1][1] == ranked[start][1]:
            stop += 1
        size = stop - start + 1
        wrong = sum(1 for _, _, is_wrong in ranked[start : stop + 1] if is_wrong)
        # Uniform tie-breaking: wrong/size per turn, whatever order the group is in.
        for step in range(1, size + 1):
            errors.append(cumulative + wrong * step / size)
        cumulative += wrong
        start = stop + 1
    # A declined turn gets no credit, so the tail rises by one a turn.
    errors.extend(cumulative + step for step in range(1, len(declined) + 1))
    return RiskCoverage(
        signal=name, direction=direction, population=config, ranked=ranked,
        errors=tuple(errors), n_declined=len(declined),
    )


def risk_coverage(config: Population, signal: Signal) -> RiskCoverage:
    return _curve_from_scores(config, signal.name, signal.direction, signal.read)


def oracle(config: Population) -> RiskCoverage:
    """Reads the grade. Named so a caller cannot mistake it for an artifact signal."""
    return _curve_from_scores(
        config, "oracle (reads the grade)", Direction.lower_first,
        lambda row: 0.0 if row.get("correct") else 1.0,
    )


def no_ranking(config: Population) -> RiskCoverage:
    """Not "accuracy equals the base rate". Abstentions still sit below every delivered turn."""
    return _curve_from_scores(config, "no ranking", Direction.lower_first, lambda _row: 0.0)


@dataclass(frozen=True)
class NestedPolicies:
    """Not a test. When one delivery set contains the other, one discordant cell is 0 by arithmetic.

    The finding is the count of useful answers lost. A p-value would restate the subset.
    """

    wider_label: str
    narrower_label: str
    n_pairs: int
    useful_wider: int
    useful_narrower: int
    withheld: int

    @property
    def lost(self) -> int:
        return self.useful_wider - self.useful_narrower

    @property
    def is_decisive(self) -> bool:
        """There is no inference here to be decisive about."""
        return False

    def render(self) -> str:
        return (
            f"{self.narrower_label} delivers a subset of {self.wider_label}'s turns "
            f"({self.withheld} withheld of {self.n_pairs}) and changes no turn's grade, so it "
            f"can only lose: {self.lost} fewer useful answer(s) "
            f"({self.useful_narrower} against {self.useful_wider}). No paired test is reported "
            "-- with the sets nested, the discordant cell one way is 0 by construction and a "
            "p-value would only restate the subset relation."
        )


def compare_policies(a: DeliveryPolicy, b: DeliveryPolicy) -> McNemarResult | NestedPolicies:
    """The outcome is delivered and correct, so a withheld answer costs.

    Nested delivery sets return ``NestedPolicies``: the engine against a cut of its own answers has an answer fixed
    before the data. Population mismatches still raise in ``mcnemar``.
    """
    left, right = a.as_population(), b.as_population()
    if (
        left.filtered_by != right.filtered_by
        or left.unit_key != right.unit_key
        or left.units != right.units
    ):
        return mcnemar(left, right, "useful_answer")  # raises, with its own diagnosis
    # ``<=`` not ``<``: the same delivery set tested against itself is a p-value of 1.
    if b.delivered <= a.delivered:
        return _nested(a, b)
    if a.delivered <= b.delivered:
        return _nested(b, a)
    return mcnemar(left, right, "useful_answer")


def _nested(wider: DeliveryPolicy, narrower: DeliveryPolicy) -> NestedPolicies:
    return NestedPolicies(
        wider_label=wider.label,
        narrower_label=narrower.label,
        n_pairs=wider.population.n,
        useful_wider=wider.useful,
        useful_narrower=narrower.useful,
        withheld=len(wider.delivered - narrower.delivered),
    )
