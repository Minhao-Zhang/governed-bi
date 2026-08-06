"""Quotability gates keyed on ``register.record.GATE_CONDITIONS``.

Three verdicts: pass / fail / cannot_evaluate. Missing fields never pass.
Refuse the comparison; do not warn (ADR 0005 §4.1). Import assert closes the
declared↔implemented sets.
"""


from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Mapping

from ..register.quantity import Measured
from ..register.record import GATE_CONDITIONS
from .degradation import channel_anomalies
from .population import Population

__all__ = ["Verdict", "GateResult", "GATE_IMPLEMENTATIONS", "evaluate", "quotable"]


class Verdict(str, Enum):
    """Three-valued on purpose; see the module docstring."""

    #: The condition held over a population large enough to have failed.
    passed = "pass"
    #: The condition was violated.
    failed = "fail"
    #: The inputs were not there. **Not a pass.** A comparison with a
    #: ``cannot_evaluate`` gate is not quotable, because the check did not happen.
    cannot_evaluate = "cannot_evaluate"


@dataclass(frozen=True)
class GateResult:
    """One gate's outcome, with everything needed to audit it."""

    field: str
    condition: str
    verdict: Verdict
    observed: Measured[float]
    population: str
    detail: str = ""

    def render(self) -> str:
        return (
            f"[{self.verdict.value:16s}] {self.field:22s} {self.observed.render(4)} "
            f"over {self.population}"
            + (f" -- {self.detail}" if self.detail else "")
        )


#: A gate takes the arm's turn records and returns its result.
GateFn = Callable[[Population], GateResult]


def _result(
    field: str,
    verdict: Verdict,
    observed: Measured[float],
    population: Population,
    detail: str = "",
) -> GateResult:
    return GateResult(
        field=field,
        condition=GATE_CONDITIONS[field],
        verdict=verdict,
        observed=observed,
        population=population.describe(),
        detail=detail,
    )


def _zero_count_gate(field: str, counter: str) -> GateFn:
    """A gate of the form "``counter`` is zero across the arm".

    Factored because four of the six gates have this shape, and four hand-written
    copies is four chances to invert a comparison — v1 shipped two
    ``LOW_CONFIDENCE_JOIN`` constants whose operators disagreed.
    """

    def gate(arm: Population) -> GateResult:
        counted = arm.count(counter)
        if not counted.is_measured:
            return _result(
                field,
                Verdict.cannot_evaluate,
                Measured.unmeasured(counted.why),
                arm,
                f"{counter!r} was not recorded on every turn, so this gate did not run",
            )
        rate = arm.rate(counter)
        if counted.value > 0:
            return _result(
                field, Verdict.failed, rate, arm, f"{counted.value} turn(s) with {counter!r}"
            )
        return _result(field, Verdict.passed, rate, arm)

    return gate


def _outcome_gate(arm: Population) -> GateResult:
    """No turn classified ``crashed``.

    The single most expensive v1 defect: a crash counted as a refusal contaminated
    every arm-to-arm delta by a *different* amount, because arms do not crash at the
    same rate. So this gate is on the classification, not on an error string.
    """
    return _zero_count_gate("outcome", "crashed")(arm)


def _facet_channels_gate(arm: Population) -> GateResult:
    """No channel state differs from its declared expectation — on turns that ran.

    The wording matters and is the reason this is not a ``_zero_count_gate``. Eight
    record fields are stage-conditional, and ``facet_channels`` is one: a
    guard-blocked turn never runs the fan-out. Under a naive rate an **empty**
    ``facet_channels`` reads as "no channel differed", i.e. as clean, on a turn where
    no channel ran at all — absence reading as agreement, in the field added to stop
    absence reading as agreement.

    So the denominator is turns where the fan-out ran, and that count is published.
    Zero such turns is :attr:`Verdict.cannot_evaluate`, never a pass.
    """
    ran = arm.restrict(lambda r: r.get("facet_channels") not in (None, [], {}), "fan-out ran")
    if ran.n == 0:
        return _result(
            "facet_channels",
            Verdict.cannot_evaluate,
            Measured.unmeasured(f"no turn in {arm.describe()} ran the fan-out"),
            arm,
            "a degradation rate of 0 over 0 turns is not a pass",
        )
    result = _zero_count_gate("facet_channels", "facet_degraded")(ran)
    if result.verdict is not Verdict.failed:
        return result
    return replace(result, detail=f"{result.detail}; {_drift(ran)}")


def _drift(arm: Population) -> str:
    """Which facet's which channel differed, for a gate that has already failed.

    The verdict comes from the stamped ``facet_degraded`` counter, not from here: this only
    names the drift, so that a refused run does not send its reader to the code. The
    judgement is ``register.facets.channel_anomaly`` through
    :func:`~.degradation.channel_anomalies` — the same function ``serve.stamp`` decides the
    counter with, because a second comparison here could disagree with the record it is
    reporting on. ``extra_channel`` is included: it is drift, it did not refuse this run,
    and a reader looking at a failure wants to see it anyway.
    """
    seen: dict[str, int] = {}
    unjudgeable = 0
    why = ""
    for row in arm.rows:
        try:
            anomalies = channel_anomalies(row.get("facet_channels"))
        except ValueError as err:
            unjudgeable += 1
            why = why or str(err)
            continue
        for key, anomaly in anomalies.items():
            seen[f"{key}={anomaly}"] = seen.get(f"{key}={anomaly}", 0) + 1
    parts = [f"{key} on {count} turn(s)" for key, count in sorted(seen.items())]
    if unjudgeable:
        parts.append(
            f"{unjudgeable} turn(s) whose facet_channels could not be judged against the "
            f"register, so their drift is not named here: {why}"
        )
    return "; ".join(parts) if parts else "no channel state differs from its declaration"


def _context_hash_gate(arm: Population) -> GateResult:
    """The delivery gate: the treatment actually differed between arms.

    L-R2. v1 ran a ladder in which two arms received byte-identical context and
    reported the difference between them as an effect. This gate is on
    ``context_hash`` and not ``delivery_hash`` because the latter depends on which
    tool calls the model chose to make, so a gate on it would conflate "the treatment
    differs" with "the model behaved differently".

    **This gate had two conditions in it and returned ``cannot_evaluate`` for both.** One is
    genuinely single-arm — every turn must *carry* a ``context_hash``, or there is nothing to
    compare later — and one is genuinely cross-arm: the >= 95% distinctness threshold needs both
    arms and lives in ``eval/report.context_hashes_distinct``. Returning
    ``cannot_evaluate`` even when coverage was complete made :func:`quotable` a function that
    **could never return True**, because ``cannot_evaluate`` blocks quotation. A permanent
    refusal is not a strict gate; it is an API no caller can use, and the observable consequence
    was that no driver called it and no number this repository published passed any gate at all.

    So the single-arm half is decided here and the cross-arm half is still refused to the
    caller that holds both arms. That preserves the actual principle — *a single-arm
    approximation of a two-arm condition is how a gate ends up measuring something adjacent to
    what it claims* — while letting the condition this arm can answer be answered.
    """
    coverage = arm.coverage("context_hash")
    # Three states, not two. **Never recorded** is an absence — the arm was not instrumented for
    # this and there is nothing to judge, which is what `cannot_evaluate` means and what an
    # uninstrumented arm must report. **Recorded on some turns and not others** is a defect: the
    # instrumentation exists and is dropping turns, so the treatment cannot be identified and a
    # later comparison against this arm is untrustworthy. Collapsing the two would either
    # excuse broken instrumentation or fail an arm for not having any.
    if not coverage.is_measured or coverage.value == 0.0:
        return _result(
            "context_hash",
            Verdict.cannot_evaluate,
            coverage,
            arm,
            "no turn carries a context_hash, so this arm is not instrumented for the "
            "delivery gate at all",
        )
    if coverage.value < 1.0:
        return _result(
            "context_hash",
            Verdict.failed,
            coverage,
            arm,
            "context_hash is recorded on some turns and missing on others, so the treatment "
            "this arm delivered cannot be identified and no later comparison against it can "
            "be trusted",
        )
    return _result(
        "context_hash",
        Verdict.passed,
        coverage,
        arm,
        "every turn carries a context_hash. The >= 95% cross-arm distinctness condition is "
        "a two-arm comparison and is evaluated by eval/report.comparison_quotable",
    )


GATE_IMPLEMENTATIONS: Mapping[str, GateFn] = {
    "outcome": _outcome_gate,
    "facet_channels": _facet_channels_gate,
    "context_hash": _context_hash_gate,
    "guardrail_errors": _zero_count_gate("guardrail_errors", "guardrail_error"),
    "n_re_served": _zero_count_gate("n_re_served", "re_served"),
    "negative": _zero_count_gate("negative", "negative_failed_open"),
}


def evaluate(arm: Population) -> tuple[GateResult, ...]:
    """Run every declared gate over one arm, in declaration order."""
    return tuple(GATE_IMPLEMENTATIONS[field](arm) for field in sorted(GATE_CONDITIONS))


def quotable(arm: Population) -> tuple[bool, tuple[GateResult, ...]]:
    """Whether this arm's numbers may be quoted, and every gate's result.

    Both are returned because a bare ``False`` sends the reader to the code to find
    out which gate refused, and a bare ``True`` hides that four gates could not run.
    **``cannot_evaluate`` blocks quotation**: a check that did not happen is not a
    check that passed.
    """
    results = evaluate(arm)
    return all(r.verdict is Verdict.passed for r in results), results


def _assert_every_declared_gate_is_implemented() -> None:
    """Import-time closure: declared GATE_CONDITIONS <-> GATE_IMPLEMENTATIONS."""
    declared, implemented = set(GATE_CONDITIONS), set(GATE_IMPLEMENTATIONS)
    if declared != implemented:  # pragma: no cover - import-time guard
        raise AssertionError(
            "quotability gates out of closure: declared-not-implemented "
            f"{sorted(declared - implemented)}, implemented-not-declared "
            f"{sorted(implemented - declared)}. v1 shipped eight of the former and "
            "every one of them passed."
        )


_assert_every_declared_gate_is_implemented()
