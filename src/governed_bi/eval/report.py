"""Eval report: Populations, McNemar, quotability, cross-arm context_hash gate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from governed_bi.measure.gates import (
    GATE_IMPLEMENTATIONS,
    GateResult,
    Verdict,
)
from governed_bi.measure.population import Population
from governed_bi.measure.stats import McNemarResult, mcnemar
from governed_bi.register.quantity import Measured
from governed_bi.register.record import GATE_CONDITIONS as _GATE_TEXT

__all__ = [
    "CONTEXT_HASH_THRESHOLD",
    "arm_population",
    "context_hashes_distinct",
    "evaluate_arm",
    "comparison_quotable",
    "headline_ex",
    "outcome_rates",
    "paired_ex",
    "summarise",
]


def _gate(
    field: str,
    verdict: Verdict,
    observed: Measured[float],
    population: Population,
    detail: str = "",
) -> GateResult:
    return GateResult(
        field=field,
        condition=_GATE_TEXT[field],
        verdict=verdict,
        observed=observed,
        population=population.describe(),
        detail=detail,
    )

CONTEXT_HASH_THRESHOLD = 0.95


def arm_population(rows: Sequence[Mapping[str, Any]], *, label: str) -> Population:
    return Population.of(label, list(rows), unit_key="question_id")


def headline_ex(arm: Population) -> Measured[float]:
    """EX rate over the arm's full population (same object McNemar must share)."""
    return arm.rate("correct")


def outcome_rates(arm: Population) -> dict[str, Measured[float]]:
    """The ``correct / clarified / refused`` scorecard a benchmark report needs
    (utku-ai-deployment-targets.md) — three named rates over the same population
    ``headline_ex`` reads, so they are directly comparable and always sum to the
    same denominator. Each is independently ``unmeasured`` if its own field is
    absent from any row, matching ``Population.rate``'s own absence handling —
    a run whose instrumentation dropped ``clarified`` on some rows must not read
    as "nothing was clarified"."""
    return {
        "correct": arm.rate("correct"),
        "clarified": arm.rate("clarified"),
        "refused": arm.rate("refused"),
    }


def paired_ex(a: Population, b: Population) -> McNemarResult:
    """McNemar on ``correct`` — populations must already share units + filters."""
    return mcnemar(a, b, "correct")


def evaluate_arm(arm: Population) -> tuple[GateResult, ...]:
    """Single-arm gates (``context_hash`` will be ``cannot_evaluate`` by design)."""
    return tuple(GATE_IMPLEMENTATIONS[field](arm) for field in sorted(_GATE_TEXT))


def context_hashes_distinct(
    a: Population,
    b: Population,
    *,
    threshold: float = CONTEXT_HASH_THRESHOLD,
) -> GateResult:
    """≥ ``threshold`` of shared questions have differing ``context_hash`` (L-R2).

    The delivery gate is on ``context_hash``, not ``delivery_hash``. Missing hashes
    on either side make the gate ``cannot_evaluate``.
    """
    if a.unit_key != b.unit_key:
        return _gate(
            "context_hash",
            Verdict.cannot_evaluate,
            Measured.unmeasured("unit keys differ"),
            a,
            f"unit keys differ: {a.unit_key!r} vs {b.unit_key!r}",
        )
    shared = a.units & b.units
    if not shared:
        return _gate(
            "context_hash",
            Verdict.cannot_evaluate,
            Measured.unmeasured("no shared questions"),
            a,
            "no shared question_ids between arms",
        )

    by_a, by_b = a.by_unit(), b.by_unit()
    comparable = 0
    distinct = 0
    missing = 0
    for qid in shared:
        ha, hb = by_a[qid].get("context_hash"), by_b[qid].get("context_hash")
        if ha is None or hb is None:
            missing += 1
            continue
        comparable += 1
        if str(ha) != str(hb):
            distinct += 1

    if missing:
        return _gate(
            "context_hash",
            Verdict.cannot_evaluate,
            Measured.unmeasured(f"{missing} shared turns lack context_hash"),
            a,
            "context_hash missing on some shared turns",
        )
    if comparable == 0:
        return _gate(
            "context_hash",
            Verdict.cannot_evaluate,
            Measured.unmeasured("no comparable hashes"),
            a,
            "no shared turns with context_hash on both arms",
        )

    rate = Measured.rate(
        distinct, comparable, what=f"context_hash distinct {a.label!r} vs {b.label!r}"
    )
    if rate.value < threshold:
        return _gate(
            "context_hash",
            Verdict.failed,
            rate,
            a,
            f"distinctness {rate.render(4)} < {threshold} "
            f"({distinct}/{comparable} shared questions)",
        )
    return _gate(
        "context_hash",
        Verdict.passed,
        rate,
        a,
        f"{distinct}/{comparable} shared questions differ",
    )


def comparison_quotable(
    a: Population,
    b: Population,
    *,
    threshold: float = CONTEXT_HASH_THRESHOLD,
) -> tuple[bool, tuple[GateResult, ...], tuple[GateResult, ...], GateResult]:
    """Whether an arm-to-arm delta may be quoted.

    Single-arm ``context_hash`` gates are replaced by
    :func:`context_hashes_distinct`. Any ``fail`` or ``cannot_evaluate`` blocks.

    The cross-arm result is computed **once** and substituted into both arms' rows. It was built
    three times, and each call stamps the population of whichever arm it was given first — so
    arm B's ``context_hash`` row reported arm **A**'s ``describe()``, on the provenance line the
    design calls load-bearing. One computation cannot disagree with itself.
    """
    ctx = context_hashes_distinct(a, b, threshold=threshold)
    results_a = _with_cross_arm_context(evaluate_arm(a), ctx)
    results_b = _with_cross_arm_context(evaluate_arm(b), ctx)
    ok = all(r.verdict is Verdict.passed for r in results_a) and all(
        r.verdict is Verdict.passed for r in results_b
    )
    return ok, results_a, results_b, ctx


def summarise(
    arms: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    pair: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Build populations, headlines, optional paired McNemar + quotability."""
    pops = {name: arm_population(rows, label=name) for name, rows in arms.items()}
    summary: dict[str, Any] = {
        "arms": {
            name: {
                "n": pop.n,
                "ex": _measured_dict(headline_ex(pop)),
                "crash_rate": _measured_dict(pop.rate("crashed")),
                "gates": [g.render() for g in evaluate_arm(pop)],
            }
            for name, pop in pops.items()
        }
    }
    if pair is not None:
        left, right = pair
        a, b = pops[left], pops[right]
        # Shared units only — explicit restrict so McNemar does not refuse.
        shared = a.units & b.units
        a_s = a.restrict(lambda r: str(r["question_id"]) in shared, "shared questions")
        b_s = b.restrict(lambda r: str(r["question_id"]) in shared, "shared questions")
        ok, ga, gb, ctx = comparison_quotable(a_s, b_s)
        summary["comparison"] = {
            "pair": pair,
            "quotable": ok,
            "context_hash_gate": ctx.render(),
            "mcnemar": paired_ex(a_s, b_s).render() if ok or a_s.n else None,
            "gates_a": [g.render() for g in ga],
            "gates_b": [g.render() for g in gb],
        }
        if a_s.n and b_s.n:
            # Always compute McNemar on the shared population for diagnostics;
            # quotation still gated by ``quotable``.
            summary["comparison"]["mcnemar"] = paired_ex(a_s, b_s).render()
            summary["comparison"]["ex_a"] = _measured_dict(headline_ex(a_s))
            summary["comparison"]["ex_b"] = _measured_dict(headline_ex(b_s))
    return summary


def _with_cross_arm_context(
    results: tuple[GateResult, ...],
    ctx: GateResult,
) -> tuple[GateResult, ...]:
    return tuple(ctx if r.field == "context_hash" else r for r in results)


def _measured_dict(m: Measured[Any]) -> dict[str, Any]:
    if not m.is_measured:
        return {"state": "unmeasured", "why": m.why, "render": m.render()}
    return {"state": "measured", "value": m.value, "render": m.render()}
