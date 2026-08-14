"""Risk-coverage curves and the operating points a caller can actually stand on.

The engine already declines: v4 delivers 1,278 of 1,351 turns and withholds 73. That
is *one point* on the coverage/accuracy plane, and a single point is all the README
quotes. This module draws the rest of the plane -- one curve per signal in
:data:`~.signals.SIGNALS` -- so the engine's mechanical policy can be read against the
alternatives instead of alone.

It is deliberately not a confidence model. ``docs/analysis/risk-coverage-v4.md`` §4
measured every structural signal on this artifact and capped them at OOF AUC 0.721,
and §6 measured an LLM critic reading the SQL at 0.597. The honest object is therefore
a plane with several curves on it, one of which is the engine's -- not a score.

Three rules the caller cannot opt out of, each from a defect already shipped here:

* **Ties are averaged, never resolved by row order.** ``n_attempts`` takes 8 distinct
  values over 1,278 delivered turns, so a curve that walks tied rows in artifact order
  reports the order the driver happened to write them in. Every point inside a tie
  group is the expectation under uniform tie-breaking, and
  :meth:`RiskCoverage.policy_at_most` will not realise a set that splits one.
* **Two things are only compared through** :func:`~.stats.mcnemar`. ``AGENTS.md``
  forbids subtracting two rates and calling it a result, and
  ``docs/analysis/audit-2026-08-10.md`` E1-E3 are three tools that did it anyway.
* **Declines are priced in** :mod:`.abstention`, **not here.** The two questions --
  how well a ranking separates right from wrong, and what the engine's own declines
  would have been worth -- have different denominators, and §4.1's is a subset the
  dataset selected rather than the arm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable

from ..register.quantity import Measured
from ..register.stages import Outcome
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

#: Smallest delivered count at which :meth:`RiskCoverage.coverage_for` will name an
#: operating point. ``risk-coverage-v4.md`` §2 already reports its 0.95 target as
#: "never (max over k >= 50 is 0.9059)" for the same reason: the largest ``k`` whose
#: accuracy clears a high target is, at small ``k``, a run of luck at the top of the
#: ranking rather than a policy anyone can operate.
MIN_OPERATING_POINT = 50

#: Outcomes in which the engine declined to answer. ``crashed`` is deliberately absent:
#: a crash is our bug, not a decision, and counting one as an abstention flatters
#: exactly the claim this module exists to test. :func:`graded` drops them and says so.
DECLINED: frozenset[str] = frozenset(
    {Outcome.refused.value, Outcome.capped.value, Outcome.clarification.value}
)


def graded(arm: Population) -> Population:
    """The population every figure in this module is computed over.

    One function, so a driver cannot compute the headline over one row set and the
    comparison over another (L-R3). Two filters, in a fixed order so two calls produce
    populations :func:`~.stats.mcnemar` accepts as the same population:

    * **crashed turns are dropped**, because a crash is an instrument failure and not a
      decision to decline. Folding them into the abstentions would credit the engine
      with declining on purpose when it fell over;
    * **turns the grader could not judge are dropped**, because ``correct is None``
      means there was no gold to compare against, and coercing it to wrong is the L-R1
      defect ``docs/measurement.md`` names outright: propagate it, do not coerce it.

    Both are recorded in ``filtered_by``, so ``describe()`` carries them to any report.
    """
    return arm.restrict(
        lambda r: r.get("outcome") != Outcome.crashed.value, "excluded crashed turns"
    ).restrict(
        lambda r: r.get("correct") is not None, "excluded turns the grader could not judge"
    )


def _split(arm: Population) -> tuple[tuple[TurnRow, ...], tuple[TurnRow, ...]]:
    """Delivered rows and declined rows, refusing on an outcome that is neither."""
    delivered = tuple(r for r in arm.rows if r.get("outcome") == Outcome.answered.value)
    declined = tuple(r for r in arm.rows if r.get("outcome") in DECLINED)
    if len(delivered) + len(declined) != arm.n:
        unknown = sorted(
            {
                str(r.get("outcome"))
                for r in arm.rows
                if r.get("outcome") != Outcome.answered.value and r.get("outcome") not in DECLINED
            }
        )
        raise ValueError(
            f"{arm.describe()} contains outcome(s) that are neither an answer nor a declared "
            f"abstention: {unknown}. Coverage is the share of turns that got an answer, so a "
            "third kind of ending has to be classified before it can be counted -- putting it "
            "silently on either side moves the headline."
        )
    return delivered, declined


@dataclass(frozen=True)
class OperatingPoint:
    """One (coverage, accuracy) pair with both of its denominators on the object.

    ``correct`` is a float, not an int: inside a tie group the curve reports the
    expectation under uniform tie-breaking, which is the only honest value when the
    signal cannot tell the tied turns apart.
    """

    label: str
    #: Every turn in the graded population -- the denominator of coverage.
    n: int
    #: Turns delivered at this point -- the denominator of accuracy.
    delivered: int
    correct: float
    population: str
    #: Non-empty when no such point exists. Both rates then report this reason.
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
    """A realised decision about which turns to answer, over a named population.

    A *set*, not a threshold: two policies are comparable only if each names the exact
    turns it delivered, and a threshold on a tied signal does not.
    """

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
        """Delivered *and* correct -- what a reader of the answers actually receives."""
        by_unit = self.population.by_unit()
        return sum(1 for u in self.delivered if bool(by_unit[u].get("correct")))

    def point(self) -> OperatingPoint:
        """Where this policy sits on the plane.

        A policy that delivers nothing still has a coverage: it is zero, and zero is a
        measurement. Its *accuracy* is unmeasured, because a rate over no answers is not
        a rate of zero. The two are kept apart rather than collapsed into an absent
        point, since "this signal can only express delivering nothing" is a finding.
        """
        return OperatingPoint(
            label=self.label,
            n=self.population.n,
            delivered=len(self.delivered),
            correct=float(self.useful),
            population=self.population.describe(),
        )

    def as_population(self) -> Population:
        """One row per turn carrying ``useful_answer``, for :func:`compare_policies`.

        ``filtered_by`` is carried over from the source rather than reset, so two
        policies built off differently-filtered arms are refused by
        :func:`~.stats.mcnemar` instead of quietly compared.
        """
        rows = [
            {
                "question_id": unit,
                "useful_answer": unit in self.delivered and bool(row.get("correct")),
            }
            for unit, row in self.population.by_unit().items()
        ]
        built = Population.of(f"{self.population.label} under {self.label}", rows)
        return replace(built, filtered_by=self.population.filtered_by)


def engine_policy(arm: Population) -> DeliveryPolicy:
    """What the engine actually did: answer the answered turns, withhold the rest."""
    delivered, _ = _split(arm)
    return DeliveryPolicy(
        label="engine (governance + attempt cap)",
        population=arm,
        delivered=frozenset(str(r[arm.unit_key]) for r in delivered),
    )


@dataclass(frozen=True)
class RiskCoverage:
    """One signal's curve over one arm.

    ``ranked`` holds the delivered turns best-first under the signal's declared
    direction. Declined turns sit below every score, because the engine already withheld
    them and no ranking can un-withhold one -- which is why every curve here passes
    through the engine's own operating point, and why no signal can beat the engine at
    the engine's coverage. Every gain costs coverage.
    """

    signal: str
    direction: Direction
    population: Population
    #: ``(question_id, score, wrong)`` for delivered turns, best-ranked first.
    ranked: tuple[tuple[str, float, bool], ...]
    #: Cumulative expected wrong answers after delivering ``k`` turns, for ``k = 1..n``.
    errors: tuple[float, ...]
    n_declined: int
    #: Non-empty when the arm does not carry this signal on every delivered turn.
    unavailable: str = ""

    @property
    def n(self) -> int:
        return self.population.n

    @property
    def aurc(self) -> Measured[float]:
        """Mean selective risk over every coverage level ``k/n``. Lower is better.

        Averaged over ``k`` rather than integrated over coverage, so the number does not
        depend on an interpolation choice. Read it beside :func:`oracle` and
        :func:`no_ranking`: an AURC on its own has no scale.
        """
        if self.unavailable:
            return Measured.unmeasured(self.unavailable)
        return Measured.rate(
            sum(e / (k + 1) for k, e in enumerate(self.errors)),
            len(self.errors),
            what=f"AURC of {self.signal!r}",
        )

    @property
    def auc(self) -> Measured[float]:
        """Rank AUC for predicting ``correct`` on delivered turns, **raw direction**.

        Raw, so it is directly comparable to ``risk-coverage-v4.md`` §4, where below 0.5
        means higher value -> more likely wrong. **It is deliberately not re-signed to
        the declared direction**, and that is what makes it worth printing beside the
        AURC: for a ``lower_first`` signal the mechanism claim holds only when this lands
        *below* 0.5, and a curve built from the claim cannot show the claim failing --
        it would simply produce a bad AURC that looks like a weak signal rather than a
        wrong one.
        """
        if self.unavailable:
            return Measured.unmeasured(self.unavailable)
        return _rank_auc([(score, not wrong) for _, score, wrong in self.ranked])

    def accuracy_at(self, coverage: float) -> OperatingPoint:
        """Selective accuracy when ``floor(coverage * n)`` turns are delivered."""
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
        """The most turns this signal can deliver at or above ``accuracy``.

        Largest ``k``, not the first: the buyer's question is how much of the workload
        survives the quality bar, and the first ``k`` to clear it is usually three
        answers at the very top of the ranking. See :data:`MIN_OPERATING_POINT`.
        """
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
        """The largest **realisable** delivery set within ``coverage``.

        Realisable means the cut falls between two distinct scores. A set that splits a
        tie group is not a policy the signal can express -- it is a coin flip among
        turns the signal cannot tell apart -- so the cut moves down to the boundary
        below it, and the achieved coverage is on the returned object rather than
        assumed. An empty set is a legitimate answer, and it is the answer for every
        ledger-derived signal here: they take 5 to 8 distinct values over 1,278 turns.
        """
        limit = math.floor(coverage * self.n)
        cut = 0
        for index in range(1, min(limit, len(self.ranked)) + 1):
            if index == len(self.ranked) or self.ranked[index][1] != self.ranked[index - 1][1]:
                cut = index
        return DeliveryPolicy(
            # `label` is presentation only, and a caller may pass a rounded coverage
            # here that this method must not round itself -- number formatting in
            # ``src/`` is ``Measured.render``'s alone (check_measurement_locality.py).
            label=label or f"{self.signal} <= cov {coverage}",
            population=self.population,
            delivered=frozenset(unit for unit, _, _ in self.ranked[:cut]),
        )

    def realisable_coverages(self) -> tuple[float, ...]:
        """Every coverage this signal can actually express, ascending.

        One entry per tie-group boundary. It is the honest measure of a signal's
        resolution, and it is why ``cuts`` belongs beside every AURC in a report: an
        AURC computed by averaging through tie groups describes a curve whose interior
        points no policy can stand on. Five boundaries over 1,278 turns is a signal that
        can be asked three questions, not a dial.
        """
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
    """Mann-Whitney AUC with mid-ranks for ties, in the raw score direction."""
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
    arm: Population, name: str, direction: Direction, score: Callable[[TurnRow], float | None]
) -> RiskCoverage:
    """Rank the delivered turns, average inside tie groups, append the declines."""
    delivered, declined = _split(arm)
    scores = [score(row) for row in delivered]
    blind = sum(1 for value in scores if value is None)
    if blind:
        return RiskCoverage(
            signal=name, direction=direction, population=arm, ranked=(), errors=(),
            n_declined=len(declined),
            unavailable=(
                f"{blind} of {len(delivered)} delivered turns carry no {name!r}, so the ranking "
                "would be over a sub-population and its AURC would not be comparable to any "
                "other signal's on this arm"
            ),
        )
    sign = 1.0 if direction is Direction.lower_first else -1.0
    scored = [
        (str(row[arm.unit_key]), float(value), not bool(row.get("correct")))
        for value, row in zip(scores, delivered)
        if value is not None
    ]
    # Stable, so tied turns keep artifact order. Nothing downstream may depend on that
    # order: the averaging below and `policy_at_most` both work on whole tie groups.
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
        # The expectation under uniform tie-breaking: a group of `size` turns holding
        # `wrong` errors contributes `wrong/size` per turn, whatever order they are in.
        for step in range(1, size + 1):
            errors.append(cumulative + wrong * step / size)
        cumulative += wrong
        start = stop + 1
    # Every declined turn is graded wrong (`docs/measurement.md`: an engine that would
    # not commit to a statement gets no credit for it), so the tail rises by one a turn.
    errors.extend(cumulative + step for step in range(1, len(declined) + 1))
    return RiskCoverage(
        signal=name, direction=direction, population=arm, ranked=ranked,
        errors=tuple(errors), n_declined=len(declined),
    )


def risk_coverage(arm: Population, signal: Signal) -> RiskCoverage:
    """The curve for one declared signal over one graded arm."""
    return _curve_from_scores(arm, signal.name, signal.direction, signal.read)


def oracle(arm: Population) -> RiskCoverage:
    """The ceiling: rank by the grade itself. Reads gold, and is named for it."""
    return _curve_from_scores(
        arm, "oracle (reads the grade)", Direction.lower_first,
        lambda row: 0.0 if row.get("correct") else 1.0,
    )


def no_ranking(arm: Population) -> RiskCoverage:
    """The null: one score for every turn, so buying coverage back buys nothing.

    Not the same as "accuracy = EX". The engine's abstentions still sit below every
    delivered turn, so this curve holds at the delivered base rate until coverage
    exceeds what the engine answered. That is the honest null for a *ranking*, and
    ``risk-coverage-v4.md`` §1 makes the same correction in prose.
    """
    return _curve_from_scores(arm, "no ranking", Direction.lower_first, lambda _row: 0.0)


@dataclass(frozen=True)
class NestedPolicies:
    """Two policies where one delivers a subset of the other's turns. **Not a test.**

    When ``b.delivered`` is a subset of ``a.delivered`` over the same population, neither
    policy changes any turn's ``correct``, so ``useful_b`` is a subset of ``useful_a`` **by
    construction**. The McNemar cell ``only_b`` is then 0 as arithmetic rather than as an
    observation, and the p-value is a deterministic function of ``only_a`` alone: any
    coverage below the wider policy's yields "decisive, in the wrong direction", however
    good or bad the ranking is. The engine-versus-ranked-policy comparison on ``v4`` was
    published as ``delta -0.1199, MDE 0.0264, p = 3.4e-49`` on exactly this shape.

    The substantive claim survives and is worth making: the trade costs 162 right answers.
    That is a count, and this object reports it as one. What is dropped is the inferential
    dress, because a reader who sees a p-value is entitled to think a null was tested.
    ``docs/analysis/audit-2026-08-10.md`` E1-E3 name the neighbouring defect -- two rates
    subtracted and called a result -- and this is its mirror image: a real test performed
    on a comparison that could not have come out any other way.
    """

    #: The wider policy: every turn ``narrower`` delivers, and generally more.
    wider_label: str
    narrower_label: str
    n_pairs: int
    #: Delivered-and-correct under each. ``lost`` is the difference and the whole finding.
    useful_wider: int
    useful_narrower: int
    #: Turns the wider policy delivers that the narrower does not.
    withheld: int

    @property
    def lost(self) -> int:
        return self.useful_wider - self.useful_narrower

    @property
    def is_decisive(self) -> bool:
        """Never. There is no inference here to be decisive about."""
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
    """Paired comparison of two policies on ``useful_answer``, via the one McNemar.

    The outcome is *delivered and correct*, which is what the reader gets: it prices a
    policy's abstentions at zero rather than excusing them, so a ranking that trades
    coverage for accuracy has to pay for the answers it withheld. Read ``is_decisive``
    and ``minimum_detectable`` before the delta -- ``open-work.md`` §3.12 puts this
    engine's floor near 2.3pp on EX, and a coverage trade is measured on the same turns.

    **Nested policies get :class:`NestedPolicies` instead**, and that is a refusal, not a
    convenience. Every risk-coverage trade on this instrument is nested by construction --
    a ranking reorders the turns the engine already agreed to answer and cannot
    un-withhold one -- so running a hypothesis test on the engine against any cut of any
    signal asks a question whose answer was fixed before the data were read. The genuine
    paired comparisons on the same page (two *different* signals at one coverage: ``a=17,
    b=3, p=0.0026``) are not nested and still go through ``mcnemar``.

    The population guards stay with ``mcnemar``: a mismatched pair raises there, before
    the nesting is examined, so a subset relation cannot excuse comparing two arms.
    """
    left, right = a.as_population(), b.as_population()
    if (
        left.filtered_by != right.filtered_by
        or left.unit_key != right.unit_key
        or left.units != right.units
    ):
        return mcnemar(left, right, "useful_answer")  # raises, with its own diagnosis
    # ``<=`` and not ``<``: two policies delivering the same set are the same policy, and
    # testing one against itself is ``open-work.md`` §3.9's "assert a constant equals itself"
    # with a p-value of 1.0 attached to make it look like evidence.
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
