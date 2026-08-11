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
from governed_bi.register.knobs import comparability_keys
from governed_bi.register.quantity import Measured
from governed_bi.register.record import GATE_CONDITIONS as _GATE_TEXT

__all__ = [
    "CONTEXT_HASH_THRESHOLD",
    "arm_population",
    "context_hashes_distinct",
    "evaluate_arm",
    "comparison_quotable",
    "headline_ex",
    "knobs_comparable",
    "paired_ex",
    "summarise",
]

#: Sentinel for "this arm's rows do not carry the key at all", which is not the same fact as
#: the key being present and ``None``. ``_resolved_knobs`` flattens ``UNSET`` to ``None`` on
#: purpose — "this run had no calibrated value" is a measurement — so recorded-``None`` is a
#: value two arms may agree on, while a missing key is the arm declining to say. A plain
#: ``dict.get`` collapses the two, and that collapse is the defect this module's
#: :func:`knobs_comparable` exists to not repeat one scope up.
_ABSENT = object()


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


def paired_ex(a: Population, b: Population) -> McNemarResult:
    """McNemar on ``correct`` — populations must already share units + filters."""
    return mcnemar(a, b, "correct")


def evaluate_arm(arm: Population) -> tuple[GateResult, ...]:
    """Single-arm gates. ``context_hash`` here only checks coverage; the cross-arm
    distinctness half is :func:`context_hashes_distinct`."""
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


def _arm_knob_values(arm: Population, keys: frozenset[str]) -> dict[str, Any]:
    """One value per key for this arm, or :data:`_ABSENT`.

    A key carrying two values across an arm's rows is *not* one configuration, but that is
    ``_knobs_resolved_gate``'s finding and it runs on both arms already. Here the whole set is
    kept so the comparison sees the disagreement rather than picking a row and calling it the
    arm — reporting ``3`` for an arm that also ran ``5`` would be the same silent-equality
    defect wearing a different hat.
    """
    out: dict[str, Any] = {}
    for key in keys:
        seen: list[Any] = []
        for row in arm.rows:
            knobs = row.get("knobs_resolved")
            if not isinstance(knobs, Mapping) or key not in knobs:
                seen.append(_ABSENT)
                continue
            value = knobs[key]
            seen.append(value)
        # `repr` for the same reason `_knobs_resolved_gate` uses it: 3 and "3" are two
        # configurations, and a comparison that coerced them would report drift as agreement.
        distinct = {repr(v) for v in seen}
        if len(distinct) == 1 and seen and seen[0] is _ABSENT:
            out[key] = _ABSENT
        else:
            out[key] = tuple(sorted(distinct))
    return out


def knobs_comparable(a: Population, b: Population) -> GateResult:
    """Both arms resolved every comparability knob to the same value.

    The between-arm half of comparability, which had no wire until 2026-08-11:
    ``comparability_keys()`` and ``config_hash_keys()`` were derived, documented, and called by
    nothing. A pair differing in ``chat_model`` was published as a delta because each arm was
    internally consistent and their contexts were distinct — neither of which is a statement
    about the treatment being the only difference.

    Three verdicts, and the middle one is the point:

    * **fail** — a key is recorded on both sides with different values. The arms are two
      treatments and the delta confounds them.
    * **cannot_evaluate** — a key is missing from an arm's rows. Absent is not a value. This is
      the distinction ``serve/session.py::_resolved_knobs`` was written to preserve, and
      collapsing it is precisely how a gate certifies a configuration it never saw.
    * **passed** — every key present on both sides and equal, recorded ``None`` included.
    """
    keys = comparability_keys()
    values_a = _arm_knob_values(a, keys)
    values_b = _arm_knob_values(b, keys)

    missing = sorted(k for k in keys if values_a[k] is _ABSENT or values_b[k] is _ABSENT)
    if missing:
        shown = ", ".join(missing[:5]) + (f" (+{len(missing) - 5} more)" if len(missing) > 5 else "")
        return _gate(
            "knobs_resolved",
            Verdict.cannot_evaluate,
            Measured.unmeasured(f"{len(missing)} comparability knobs absent from an arm"),
            a,
            f"not recorded on both arms, so equality cannot be claimed: {shown}",
        )

    differing = sorted(k for k in keys if values_a[k] != values_b[k])
    if differing:
        shown = "; ".join(
            f"{k}: {'/'.join(values_a[k])} vs {'/'.join(values_b[k])}" for k in differing[:4]
        )
        extra = f" (+{len(differing) - 4} more)" if len(differing) > 4 else ""
        return _gate(
            "knobs_resolved",
            Verdict.failed,
            Measured.rate(len(keys) - len(differing), len(keys),
                          what="comparability knobs agreeing across arms"),
            a,
            f"arms differ on {len(differing)} comparability knob(s): {shown}{extra}",
        )

    return _gate(
        "knobs_resolved",
        Verdict.passed,
        Measured.rate(len(keys), len(keys),
                      what="comparability knobs agreeing across arms"),
        a,
        f"both arms agree on all {len(keys)} comparability knobs",
    )


def comparison_quotable(
    a: Population,
    b: Population,
    *,
    threshold: float = CONTEXT_HASH_THRESHOLD,
) -> tuple[bool, tuple[GateResult, ...], tuple[GateResult, ...], GateResult, GateResult]:
    """Whether an arm-to-arm delta may be quoted.

    Two cross-arm gates, because a pair can fail comparability in two independent ways and
    reporting one would hide the other. :func:`context_hashes_distinct` asks whether the arms
    did anything *different*; :func:`knobs_comparable` asks whether the difference is only the
    treatment. An arm pair needs both, and until 2026-08-11 only the first was asked — see
    that function for what the omission allowed.

    Single-arm ``context_hash`` gates are replaced by the cross-arm one. The ``knobs_resolved``
    gate is **not** replaced: each arm's own within-arm homogeneity is a different question
    from the two arms agreeing with each other, and both must hold.

    Computed **once** and substituted into both arms' rows: each call stamps the population
    of whichever arm it was given first, so recomputing per arm makes arm B's row report arm
    A's ``describe()`` on the provenance line.
    """
    ctx = context_hashes_distinct(a, b, threshold=threshold)
    knobs = knobs_comparable(a, b)
    results_a = _with_cross_arm_context(evaluate_arm(a), ctx)
    results_b = _with_cross_arm_context(evaluate_arm(b), ctx)
    ok = (
        knobs.verdict is Verdict.passed
        and all(r.verdict is Verdict.passed for r in results_a)
        and all(r.verdict is Verdict.passed for r in results_b)
    )
    return ok, results_a, results_b, ctx, knobs


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
        ok, ga, gb, ctx, knobs = comparison_quotable(a_s, b_s)
        summary["comparison"] = {
            "pair": pair,
            "quotable": ok,
            "context_hash_gate": ctx.render(),
            "knobs_comparable_gate": knobs.render(),
            "mcnemar": paired_ex(a_s, b_s).render() if ok or a_s.n else None,
            "gates_a": [g.render() for g in ga],
            "gates_b": [g.render() for g in gb],
        }
        if a_s.n and b_s.n:
            # ``mcnemar`` is already set above, on the wider condition — computing it for
            # diagnostics whatever ``quotable`` says is that line's job, not this block's.
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
