"""Populations, McNemar, quotability, and why a config did not answer.

The refusal histogram reads the declared reasons (ADR 0013). A reason in no
register lands in ``unattributed``, so a vocabulary nothing else reads is
visible on the artifact instead of absorbed into the total.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from governed_bi.eval.projection import ASSUMPTION_BASES
from governed_bi.measure.checks import (
    CHECK_IMPLEMENTATIONS,
    CheckResult,
    Result,
)
from governed_bi.measure.population import Population
from governed_bi.measure.stats import McNemarResult, mcnemar
from governed_bi.registry.quantity import Measured
from governed_bi.registry.record import CHECK_CONDITIONS as _CHECK_TEXT
from governed_bi.registry.settings import setting_names
from governed_bi.registry.stages import REFUSED_BY_TO_STAGE, Outcome
from governed_bi.registry.stored_names import artifact_get, current_reason

__all__ = [
    "CONTEXT_HASH_THRESHOLD",
    "REFUSAL_CHANNELS",
    "config_population",
    "context_hashes_distinct",
    "evaluate_config",
    "comparison_quotable",
    "headline_ex",
    "settings_comparable",
    "outcome_rates",
    "paired_ex",
    "assumption_figures",
    "assumption_report_lines",
    "refusal_histogram",
    "refusal_report_lines",
    "summarise",
]

#: Where a refused turn's reason is written, in this order. ``terminal_reason``
#: is the rule; ``refused_by`` is the stage. A row with both is counted once.
REFUSAL_CHANNELS: tuple[str, ...] = ("terminal_reason", "refused_by")

#: The key is missing, which is not a recorded ``None``. ``None`` is a value two
#: configs may agree on. ``dict.get`` collapses the two.
_ABSENT = object()


def _check(
    field: str,
    result: Result,
    observed: Measured[float],
    population: Population,
    detail: str = "",
) -> CheckResult:
    return CheckResult(
        field=field,
        condition=_CHECK_TEXT[field],
        result=result,
        observed=observed,
        population=population.describe(),
        detail=detail,
    )

CONTEXT_HASH_THRESHOLD = 0.95


def config_population(rows: Sequence[Mapping[str, Any]], *, label: str) -> Population:
    return Population.of(label, list(rows), unit_key="question_id")


def headline_ex(config: Population) -> Measured[float]:
    """EX rate over the config's full population (same object McNemar must share)."""
    return config.rate("correct")


def outcome_rates(config: Population) -> dict[str, Measured[float]]:
    """``correct``, ``clarified``, and ``refused`` over the same population.

    Each is unmeasured if its own field is absent. A missing ``clarified``
    must not read as nothing was clarified.
    """
    return {
        "correct": config.rate("correct"),
        "clarified": config.rate("clarified"),
        "refused": config.rate("refused"),
    }


def paired_ex(a: Population, b: Population) -> McNemarResult:
    """McNemar on ``correct`` — populations must already share units + filters."""
    return mcnemar(a, b, "correct")


def refusal_histogram(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """By reason and stage, over refused rows. A reason in no register is ``unattributed``, left out of ``by_stage``."""
    by_reason: dict[str, int] = {}
    unattributed: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    n_refused = 0
    no_reason = 0
    for row in rows:
        if str(row.get("outcome") or "") != Outcome.refused.value:
            continue
        n_refused += 1
        reason = next(
            (str(row[c]) for c in REFUSAL_CHANNELS if row.get(c) not in (None, "")), ""
        )
        if not reason:
            no_reason += 1
            continue
        reason = current_reason(reason)
        by_reason[reason] = by_reason.get(reason, 0) + 1
        stage = REFUSED_BY_TO_STAGE.get(reason)
        if stage is None:
            unattributed[reason] = unattributed.get(reason, 0) + 1
            continue
        by_stage[stage.value] = by_stage.get(stage.value, 0) + 1
    return {
        "n_rows": len(rows),
        "n_refused": n_refused,
        "by_reason": dict(sorted(by_reason.items())),
        "by_stage": dict(sorted(by_stage.items())),
        "unattributed": dict(sorted(unattributed.items())),
        "no_reason": no_reason,
    }


def refusal_report_lines(hist: Mapping[str, Any]) -> list[str]:
    """Printable lines, or nothing when nothing refused.

    The header carries the refused count and the population. ``UNATTRIBUTED``
    is marked because a reason in no register means the lines do not add up.
    """
    if not hist.get("n_refused"):
        return []
    lines = [
        f"\nrefused turns by declared reason ({hist['n_refused']} of {hist['n_rows']}):",
        *(
            f"  {name:<44}{n:>6}"
            for name, n in sorted(hist["by_reason"].items(), key=lambda kv: (-kv[1], kv[0]))[:12]
        ),
        "  by stage: " + (", ".join(f"{k}={v}" for k, v in hist["by_stage"].items()) or "-"),
    ]
    if hist["unattributed"]:
        lines.append(
            "  UNATTRIBUTED (in no register): "
            + ", ".join(f"{k}={v}" for k, v in hist["unattributed"].items())
        )
    if hist["no_reason"]:
        lines.append(f"  refused with no reason recorded: {hist['no_reason']}")
    return lines


def _plain_int(value: Any) -> int | None:
    """An int, or ``None``. ``bool`` is not a count (``True`` would read as one guess)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _recorded_answers(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], int]:
    """Answered rows whose assumption counts were written, and how many answers were not."""
    recorded: list[Mapping[str, Any]] = []
    not_recorded = 0
    for row in rows:
        if str(row.get("outcome") or "") != Outcome.answered.value:
            continue
        if isinstance(row.get("assumption_counts"), Mapping):
            recorded.append(row)
        else:
            not_recorded += 1
    return recorded, not_recorded


def _basis_value(row: Mapping[str, Any], basis: str) -> int | None:
    counts = row.get("assumption_counts")
    if not isinstance(counts, Mapping):
        return None
    return _plain_int(counts.get(basis))


def _basis_mean(recorded: Sequence[Mapping[str, Any]], basis: str) -> dict[str, Any]:
    """Total and mean, or both ``None`` when any answer left this basis unwritten."""
    values = [_basis_value(row, basis) for row in recorded]
    known = [value for value in values if value is not None]
    if not recorded or len(known) != len(recorded):
        return {"total": None, "mean": None}
    total = sum(known)
    return {"total": total, "mean": total / len(recorded)}


def _execution_accuracy(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """``correct`` over this split, with the same absence rule as the headline rate.

    One ungraded answer makes the accuracy unmeasured. Counting it wrong would
    mix a missing gold in with answers the model missed.
    """
    n = len(rows)
    if n == 0 or any(row.get("correct") is None for row in rows):
        return {"n": n, "n_correct": None, "accuracy": None}
    n_correct = sum(1 for row in rows if row["correct"])
    return {"n": n, "n_correct": n_correct, "accuracy": n_correct / n}


def assumption_figures(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per-answer means, guess accuracy, and server-added ``found_in_query`` entries.

    Answered rows with no ``assumption_counts`` count as not recorded. They stay
    out of the means: a missing list is not an answer that stated nothing.
    """
    recorded, not_recorded = _recorded_answers(rows)
    guessed: list[Mapping[str, Any]] = []
    unguessed: list[Mapping[str, Any]] = []
    for row in recorded:
        guesses = _basis_value(row, "guess")
        if guesses is None:
            continue
        (guessed if guesses > 0 else unguessed).append(row)
    # Every found_in_query entry is one the server added, so the total is what the model did not state.
    found = [_basis_value(row, "found_in_query") for row in recorded]
    known_found = [value for value in found if value is not None]
    if not recorded or len(known_found) != len(recorded):
        unstated: dict[str, Any] = {"total": None, "n_with_any": None, "share": None}
    else:
        n_with_any = sum(1 for value in known_found if value > 0)
        unstated = {
            "total": sum(known_found),
            "n_with_any": n_with_any,
            "share": n_with_any / len(recorded),
        }
    # Every turn, not just answers: a refused guess usually ends the turn in a clarification.
    refusals = [_plain_int(row.get("ambiguous_guess_refusals")) for row in rows]
    known_refusals = [value for value in refusals if value is not None]
    refusal_total = sum(known_refusals) if known_refusals else None
    return {
        "n_answers": len(recorded),
        "not_recorded": not_recorded,
        "by_basis": {basis: _basis_mean(recorded, basis) for basis in ASSUMPTION_BASES},
        "with_guess": _execution_accuracy(guessed),
        "without_guess": _execution_accuracy(unguessed),
        "found_in_query_unstated": unstated,
        "ambiguous_guess_refusals": refusal_total,
    }


def assumption_report_lines(figures: Mapping[str, Any]) -> list[str]:
    """Printable lines for :func:`assumption_figures`. No lines when nothing was answered."""
    n = int(figures["n_answers"])
    missing = int(figures["not_recorded"])
    if n == 0 and missing == 0:
        return []
    lines = [f"\nassumptions per answer ({n} recorded, {missing} not recorded):"]
    if n == 0:
        return lines
    for basis in ASSUMPTION_BASES:
        entry = figures["by_basis"][basis]
        shown = "not recorded" if entry["total"] is None else f"{entry['total']}/{n}"
        lines.append(f"  {basis}: {shown}")
    for label, key in (("with a guess", "with_guess"), ("with no guess", "without_guess")):
        group = figures[key]
        if group["n"] == 0:
            shown = "n=0"
        elif group["accuracy"] is None:
            shown = f"not recorded (n={group['n']})"
        else:
            shown = f"{group['n_correct']}/{group['n']} (n={group['n']})"
        lines.append(f"  execution accuracy {label}: {shown}")
    unstated = figures["found_in_query_unstated"]
    if unstated["total"] is None:
        lines.append("  found_in_query the model did not state: not recorded")
    else:
        lines.append(
            "  found_in_query the model did not state: "
            f"{unstated['total']} ({unstated['n_with_any']}/{n} answers)"
        )
    refusals = figures["ambiguous_guess_refusals"]
    shown = "not recorded" if refusals is None else str(refusals)
    lines.append(f"  ambiguous_guess_refusals: {shown}")
    return lines


def evaluate_config(config: Population) -> tuple[CheckResult, ...]:
    """Single-config checks. ``context_hash`` here only checks coverage; the cross-config
    distinctness half is :func:`context_hashes_distinct`."""
    return tuple(CHECK_IMPLEMENTATIONS[field](config) for field in sorted(_CHECK_TEXT))


def context_hashes_distinct(
    a: Population,
    b: Population,
    *,
    threshold: float = CONTEXT_HASH_THRESHOLD,
) -> CheckResult:
    """Both configs recorded a ``context_hash`` on shared questions. Differing hashes are retrieval noise. ``threshold``
    is unused; callers still pass it."""
    if a.unit_key != b.unit_key:
        return _check(
            "context_hash",
            Result.cannot_evaluate,
            Measured.unmeasured("unit keys differ"),
            a,
            f"unit keys differ: {a.unit_key!r} vs {b.unit_key!r}",
        )
    shared = a.units & b.units
    if not shared:
        return _check(
            "context_hash",
            Result.cannot_evaluate,
            Measured.unmeasured("no shared questions"),
            a,
            "no shared question_ids between configs",
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
        return _check(
            "context_hash",
            Result.cannot_evaluate,
            Measured.unmeasured(f"{missing} shared turns lack context_hash"),
            a,
            "context_hash missing on some shared turns",
        )
    if comparable == 0:
        return _check(
            "context_hash",
            Result.cannot_evaluate,
            Measured.unmeasured("no comparable hashes"),
            a,
            "no shared turns with context_hash on both configs",
        )

    rate = Measured.rate(
        comparable, len(shared), what=f"context_hash recorded {a.label!r} vs {b.label!r}"
    )
    return _check(
        "context_hash",
        Result.passed,
        rate,
        a,
        f"both configs assembled a context on all {comparable} shared questions "
        f"({distinct} of them differ; distinctness is no longer the result — audit D9 — and "
        f"the retired threshold was {threshold})",
    )


def _config_setting_values(config: Population, keys: frozenset[str]) -> dict[str, Any]:
    """One value per key, or :data:`_ABSENT` when the key is missing.

    Two values on one config are kept as a pair. Picking one would report
    agreement with a config that also ran the other value.
    """
    out: dict[str, Any] = {}
    for key in keys:
        seen: list[Any] = []
        for row in config.rows:
            settings = artifact_get(row, "settings_resolved")
            if not isinstance(settings, Mapping) or key not in settings:
                seen.append(_ABSENT)
                continue
            value = settings[key]
            seen.append(value)
        # `repr`: 3 and "3" are two configurations. Coercing them would report drift as agreement.
        distinct = {repr(v) for v in seen}
        if len(distinct) == 1 and seen and seen[0] is _ABSENT:
            out[key] = _ABSENT
        else:
            out[key] = tuple(sorted(distinct))
    return out


def settings_comparable(
    a: Population,
    b: Population,
    *,
    under_test: frozenset[str] = frozenset(),
) -> CheckResult:
    """The declared change differs, and every other result-affecting setting agrees.

    Hash differences are retrieval noise. An empty declaration means nobody
    named the change. A missing key is not a recorded ``None``.
    """
    keys = setting_names()

    undeclared = sorted(under_test - keys)
    if undeclared:
        return _check(
            "settings_resolved",
            Result.cannot_evaluate,
            Measured.unmeasured("treatment names a setting that is not declared"),
            a,
            "declared treatment is not in setting_names(): " + ", ".join(undeclared),
        )

    values_a = _config_setting_values(a, keys)
    values_b = _config_setting_values(b, keys)

    missing = sorted(k for k in keys if values_a[k] is _ABSENT or values_b[k] is _ABSENT)
    if missing:
        shown = ", ".join(missing[:5]) + (f" (+{len(missing) - 5} more)" if len(missing) > 5 else "")
        return _check(
            "settings_resolved",
            Result.cannot_evaluate,
            Measured.unmeasured(f"{len(missing)} comparability settings absent from a config"),
            a,
            f"not recorded on both configs, so equality cannot be claimed: {shown}",
        )

    confounders = keys - under_test
    differing = sorted(k for k in confounders if values_a[k] != values_b[k])
    if differing:
        shown = "; ".join(
            f"{k}: {'/'.join(values_a[k])} vs {'/'.join(values_b[k])}" for k in differing[:4]
        )
        extra = f" (+{len(differing) - 4} more)" if len(differing) > 4 else ""
        return _check(
            "settings_resolved",
            Result.failed,
            Measured.rate(
                len(confounders) - len(differing),
                len(confounders),
                what="non-treatment comparability settings agreeing across configs",
            ),
            a,
            f"configs differ on {len(differing)} setting(s) outside the declared treatment: "
            f"{shown}{extra}",
        )

    if not under_test:
        return _check(
            "settings_resolved",
            Result.cannot_evaluate,
            Measured.unmeasured("no treatment declared"),
            a,
            "no treatment declared, so this pair cannot be shown to be a comparison rather "
            "than a replicate — every comparability setting is identical across the two configs",
        )

    unmoved = sorted(k for k in under_test if values_a[k] == values_b[k])
    if unmoved:
        return _check(
            "settings_resolved",
            Result.failed,
            Measured.rate(
                len(under_test) - len(unmoved),
                len(under_test),
                what="declared treatment settings that actually differ",
            ),
            a,
            "the declared treatment is identical on both configs, so this is a replicate and not "
            "a comparison: " + ", ".join(unmoved),
        )

    return _check(
        "settings_resolved",
        Result.passed,
        Measured.rate(
            len(confounders),
            len(confounders),
            what="non-treatment comparability settings agreeing across configs",
        ),
        a,
        f"treatment {sorted(under_test)} differs; all {len(confounders)} other comparability "
        "settings agree",
    )


def comparison_quotable(
    a: Population,
    b: Population,
    *,
    threshold: float = CONTEXT_HASH_THRESHOLD,
    under_test: frozenset[str] = frozenset(),
) -> tuple[bool, tuple[CheckResult, ...], tuple[CheckResult, ...], CheckResult, CheckResult]:
    """Whether a delta may be quoted. Context hashes must exist and do not show what changed. Computed once, or the
    second call describes the first config."""
    ctx = context_hashes_distinct(a, b, threshold=threshold)
    settings = settings_comparable(a, b, under_test=under_test)
    results_a = _with_cross_config_context(evaluate_config(a), ctx)
    results_b = _with_cross_config_context(evaluate_config(b), ctx)
    ok = (
        settings.result is Result.passed
        and all(r.result is Result.passed for r in results_a)
        and all(r.result is Result.passed for r in results_b)
    )
    return ok, results_a, results_b, ctx, settings


def summarise(
    configs: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    pair: tuple[str, str] | None = None,
    under_test: frozenset[str] | None = None,
) -> dict[str, Any]:
    """The declared change comes from ``configs.toml`` when omitted. No profile means it cannot be evaluated."""
    pops = {name: config_population(rows, label=name) for name, rows in configs.items()}
    summary: dict[str, Any] = {
        "configs": {
            name: {
                "n": pop.n,
                "ex": _measured_dict(headline_ex(pop)),
                "crash_rate": _measured_dict(pop.rate("crashed")),
                # Per config: why this config declined is not a property of the pair.
                "refusals": refusal_histogram(pop.rows),
                "assumptions": assumption_figures(pop.rows),
                "checks": [g.render() for g in evaluate_config(pop)],
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
        declared = under_test if under_test is not None else _declared_under_test(right)
        ok, ga, gb, ctx, settings = comparison_quotable(a_s, b_s, under_test=declared)
        summary["comparison"] = {
            "pair": pair,
            "quotable": ok,
            "context_hash_check": ctx.render(),
            "settings_comparable_check": settings.render(),
            "treatment": sorted(declared),
            # Computed even when the pair is not quotable. The string says so, because
            # a line copied out of the report leaves the nearby flag behind.
            "mcnemar": _rendered_mcnemar(paired_ex(a_s, b_s), quotable=ok) if a_s.n else None,
            "checks_a": [g.render() for g in ga],
            "checks_b": [g.render() for g in gb],
        }
        if a_s.n and b_s.n:
            # Rates only. The paired statistic is set above, including when it is not quotable.
            summary["comparison"]["ex_a"] = _measured_dict(headline_ex(a_s))
            summary["comparison"]["ex_b"] = _measured_dict(headline_ex(b_s))
    return summary


def _declared_under_test(config_name: str) -> frozenset[str]:
    """The declared change from ``configs.toml``, or empty when there is no profile.

    Empty is not a pass. ``ValueError`` and ``OSError`` propagate: swallowing a
    malformed file would report every comparison as unevaluable data.
    """
    from governed_bi.registry.config_profiles import config_profile

    try:
        return config_profile(config_name).under_test
    except KeyError:
        return frozenset()


def _with_cross_config_context(
    results: tuple[CheckResult, ...],
    ctx: CheckResult,
) -> tuple[CheckResult, ...]:
    return tuple(ctx if r.field == "context_hash" else r for r in results)


def _rendered_mcnemar(result: Any, *, quotable: bool) -> str:
    """The paired line says whether it may be quoted. A line copied out of the report leaves the nearby flag behind."""
    line = result.render()
    return line if quotable else f"{line}  [NOT QUOTABLE: a check did not pass]"


def _measured_dict(m: Measured[Any]) -> dict[str, Any]:
    if not m.is_measured:
        return {"state": "unmeasured", "why": m.why, "render": m.render()}
    return {"state": "measured", "value": m.value, "render": m.render()}
