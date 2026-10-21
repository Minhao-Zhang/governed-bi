"""Eval report: Populations, McNemar, quotability, cross-config context_hash check.

Also the **refusal histogram** — "why did this config not answer", counted in the vocabulary
``registry/stages.py`` declares. ADR 0013 §2 argued for putting the abstention reasons in
``REFUSED_BY_TO_STAGE`` on the grounds that ``classify_outcome``, a refusal histogram and this
module already read that table. Measured on 2026-08-12, all three were false:

* ``classify_outcome`` never consults it. Any truthy ``refused_by`` returns
  ``Outcome.refused``, declared or not.
* A ledger histogram counts ``attempt.reason_code`` and never touches the table. A withheld
  turn writes no ledger row — ADR 0013's acceptance criterion 3 — so the four abstention
  reasons are unreachable from the ledger.
* This module had zero references to ``refused_by``, ``terminal_reason`` or the vocabulary.

Three named consumers, none of them real: the declared-machinery-with-no-reader shape the
argument itself was invoking. The histogram is built here rather than the sentence weakened, and
:func:`refusal_histogram`'s ``unattributed`` bucket is the part that makes the vocabulary
load-bearing instead of decorative.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

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
    "refusal_histogram",
    "refusal_report_lines",
    "summarise",
]

#: Where a refused turn's reason is written, in the order the histogram prefers them.
#:
#: ``terminal_reason`` first: ``route``, ``connect`` and the abstention policy all write the
#: *rule* there, while ``refused_by`` names the stage and is coarser (``"guardrail"`` for every
#: layer refusal). A row carrying both is one decision, so it is counted once.
REFUSAL_CHANNELS: tuple[str, ...] = ("terminal_reason", "refused_by")

#: Sentinel for "this config's rows do not carry the key at all", which is not the same fact as
#: the key being present and ``None``. ``_resolved_settings`` flattens ``UNSET`` to ``None`` on
#: purpose — "this run had no calibrated value" is a measurement — so recorded-``None`` is a
#: value two configs may agree on, while a missing key is the config declining to say. A plain
#: ``dict.get`` collapses the two, and that collapse is the defect this module's
#: :func:`settings_comparable` exists to not repeat one scope up.
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
    """The ``correct / clarified / refused`` scorecard a benchmark report needs — three
    named rates over the same population
    ``headline_ex`` reads, so they are directly comparable and always sum to the
    same denominator. Each is independently ``unmeasured`` if its own field is
    absent from any row, matching ``Population.rate``'s own absence handling —
    a run whose instrumentation dropped ``clarified`` on some rows must not read
    as "nothing was clarified"."""
    return {
        "correct": config.rate("correct"),
        "clarified": config.rate("clarified"),
        "refused": config.rate("refused"),
    }


def paired_ex(a: Population, b: Population) -> McNemarResult:
    """McNemar on ``correct`` — populations must already share units + filters."""
    return mcnemar(a, b, "correct")


def refusal_histogram(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Why this config did not answer, counted by reason and by the stage that owns each reason.

    **The point of the ``unattributed`` bucket.** ``REFUSED_BY_TO_STAGE`` is called a closed
    vocabulary, and two import-time guards keep the *declarations* in step with each other — but
    nothing before this read the table against real rows, so a node writing a string that is in
    neither the register nor any guard produced a refusal that every count still absorbed
    silently. Here it lands in its own bucket with its own name, which is what "closed" has to
    mean once artifacts exist: a reader can see that the histogram does not add up and see which
    string is why.

    ``by_stage`` is the question §4.2 of open-work.md asks — *retrieval missed or the engine
    decided to withhold* — and it is answerable because ADR 0013 put the abstention reasons in the
    same table. Unattributed reasons are **not** in ``by_stage``: there is no stage to credit,
    and inventing one is the misattribution that ADR exists to end.

    Counted over the rows the config classified ``refused``. A crash is not a refusal (``Outcome``
    keeps them apart), a cap is ``capped``, and a clarification is its own outcome — so a
    histogram over every row would answer a different question from the one it is named for.
    """
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
    """:func:`refusal_histogram` as printable lines, or **nothing** when nothing refused.

    Beside the histogram rather than in the driver, for ``adversarial_run.py``'s reason: a
    driver and a test both need the rendering while only the reader needs the counts, and the
    two have different lifecycles.

    The header carries the refused count and the population it came out of, so no line below
    is a rate whose denominator a reader has to go and find. Those lines are raw counts, at
    most twelve of them, and they sum to the header with ``no_reason``. ``UNATTRIBUTED`` is
    shouted because a reason in no register means the numbers below it do not add up.
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
    """Both configs recorded a ``context_hash`` on the questions they share.

    **An existence check, not a treatment test — that is audit D9's resolution.** It used to
    require ``threshold`` (0.95) of shared questions to have *differing* hashes, on the
    reasoning that a changed treatment changes the context. The inference does not hold in
    that direction: retrieval is nondeterministic, so the hashes differ whether or not the
    treatment did. Measured on ``run1``/``run2``, which differ only by a random seed, it passed
    at **0.9993** (1,350 of 1,351 hashes differ) — and on the 20 other pairs of the seven
    ``proxy_*`` configs in ``runs/eval/`` it never falls below **0.9882**, hitting exactly 1.0000
    on 11 of them (corpus ``30872d3``, recomputed 2026-08-12). It believed it asked "did the
    treatment change" and measured "is there retrieval noise", to which the answer is always
    yes.

    What replaces it is :func:`settings_comparable`, which reads the declared treatment out of the
    settings rather than inferring it from a hash. This function keeps the narrower job it can
    actually do: a turn with no ``context_hash`` assembled no context, and a pair of configs where
    that is true of some shared questions cannot be compared on those questions at all.

    ``threshold`` is retained in the signature and deliberately unused by the result, because
    callers pass it and a silent behaviour change under an unchanged call is worse than a
    parameter that documents its own retirement. It is reported in the detail line.
    """
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
    """One value per key for this config, or :data:`_ABSENT`.

    A key carrying two values across a config's rows is *not* one configuration, but that is
    ``_knobs_resolved_check``'s finding and it runs on both configs already. Here the whole set is
    kept so the comparison sees the disagreement rather than picking a row and calling it the
    config — reporting ``3`` for a config that also ran ``5`` would be the same silent-equality
    defect wearing a different hat.
    """
    out: dict[str, Any] = {}
    for key in keys:
        seen: list[Any] = []
        for row in config.rows:
            settings = row.get("knobs_resolved")
            if not isinstance(settings, Mapping) or key not in settings:
                seen.append(_ABSENT)
                continue
            value = settings[key]
            seen.append(value)
        # `repr` for the same reason `_knobs_resolved_check` uses it: 3 and "3" are two
        # configurations, and a comparison that coerced them would report drift as agreement.
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
    treatment: frozenset[str] = frozenset(),
) -> CheckResult:
    """The configs differ in the declared treatment, and in nothing else that matters.

    Both halves are necessary and the second one is audit D9's fix.

    **Confounders.** Every comparability setting *except* the declared treatment must be recorded
    on both configs and equal. Until 2026-08-11 nothing checked this: ``setting_names()``
    and ``setting_names()` had no production caller, so a pair differing in ``chat_model``
    was published as a delta because each config was internally homogeneous.

    **The treatment itself must have moved.** A comparison whose declared treatment is
    identical on both configs is a replicate wearing a config's name. This is what D9 asked for:
    treatment difference asserted from *declared fields* rather than inferred from
    ``context_hash`` noise, which measured retrieval nondeterminism and passed at 0.9993 on a
    seed-only null pair.

    ``treatment`` names comparability settings — a treatment that is not one is a category error
    and refuses rather than being quietly ignored. An **empty** ``treatment`` is not "nothing
    changed", it is "nobody said what changed", and a pair that cannot name its treatment
    cannot be shown to be a comparison rather than a replicate.

    Absent and ``None`` stay apart throughout: ``_resolved_settings`` flattens ``UNSET`` to
    ``None`` on purpose, so two configs may agree on a recorded ``None``, while a key missing from
    the mapping is the config declining to say. Collapsing them is how the within-config check once
    certified a configuration it never saw.
    """
    keys = setting_names()

    undeclared = sorted(treatment - keys)
    if undeclared:
        return _check(
            "knobs_resolved",
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
            "knobs_resolved",
            Result.cannot_evaluate,
            Measured.unmeasured(f"{len(missing)} comparability settings absent from a config"),
            a,
            f"not recorded on both configs, so equality cannot be claimed: {shown}",
        )

    confounders = keys - treatment
    differing = sorted(k for k in confounders if values_a[k] != values_b[k])
    if differing:
        shown = "; ".join(
            f"{k}: {'/'.join(values_a[k])} vs {'/'.join(values_b[k])}" for k in differing[:4]
        )
        extra = f" (+{len(differing) - 4} more)" if len(differing) > 4 else ""
        return _check(
            "knobs_resolved",
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

    if not treatment:
        return _check(
            "knobs_resolved",
            Result.cannot_evaluate,
            Measured.unmeasured("no treatment declared"),
            a,
            "no treatment declared, so this pair cannot be shown to be a comparison rather "
            "than a replicate — every comparability setting is identical across the two configs",
        )

    unmoved = sorted(k for k in treatment if values_a[k] == values_b[k])
    if unmoved:
        return _check(
            "knobs_resolved",
            Result.failed,
            Measured.rate(
                len(treatment) - len(unmoved),
                len(treatment),
                what="declared treatment settings that actually differ",
            ),
            a,
            "the declared treatment is identical on both configs, so this is a replicate and not "
            "a comparison: " + ", ".join(unmoved),
        )

    return _check(
        "knobs_resolved",
        Result.passed,
        Measured.rate(
            len(confounders),
            len(confounders),
            what="non-treatment comparability settings agreeing across configs",
        ),
        a,
        f"treatment {sorted(treatment)} differs; all {len(confounders)} other comparability "
        "settings agree",
    )


def comparison_quotable(
    a: Population,
    b: Population,
    *,
    threshold: float = CONTEXT_HASH_THRESHOLD,
    treatment: frozenset[str] = frozenset(),
) -> tuple[bool, tuple[CheckResult, ...], tuple[CheckResult, ...], CheckResult, CheckResult]:
    """Whether a config-to-config delta may be quoted.

    Two cross-config checks with different jobs. :func:`context_hashes_distinct` is now an
    **existence** check — both configs assembled a context on the questions they share — because
    audit D9 established that its old 95%-distinctness threshold measured retrieval
    nondeterminism and reported it as a treatment difference, passing at 0.9993 on a pair that
    differs only by a random seed. Judging the treatment is :func:`settings_comparable`'s job, from
    declared settings, and ``treatment`` is how the caller names what is supposed to have moved.

    Single-config ``context_hash`` checks are replaced by the cross-config one. The ``knobs_resolved``
    check is **not** replaced: each config's own within-config homogeneity is a different question
    from the two configs agreeing with each other, and both must hold.

    Computed **once** and substituted into both configs' rows: each call stamps the population
    of whichever config it was given first, so recomputing per config makes config B's row report config
    A's ``describe()`` on the provenance line.
    """
    ctx = context_hashes_distinct(a, b, threshold=threshold)
    settings = settings_comparable(a, b, treatment=treatment)
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
    treatment: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Build populations, headlines, optional paired McNemar + quotability.

    ``treatment`` names the comparability settings the pair's second config was supposed to move.
    Left ``None`` it is read from ``configs.toml`` via the config's own name, which is the point of
    that file: the claim about what a config changed is committed and diffable rather than living
    in whoever ran the command. An config with no profile contributes no treatment, and
    ``settings_comparable`` then reports ``cannot_evaluate`` rather than guessing — see D9.
    """
    pops = {name: config_population(rows, label=name) for name, rows in configs.items()}
    summary: dict[str, Any] = {
        "configs": {
            name: {
                "n": pop.n,
                "ex": _measured_dict(headline_ex(pop)),
                "crash_rate": _measured_dict(pop.rate("crashed")),
                # The consumer ADR 0013 §2 named and did not have. Per config and not per pair:
                # "why did this config decline" is a description of one config, and the paired block
                # below is about a difference.
                "refusals": refusal_histogram(pop.rows),
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
        declared = treatment if treatment is not None else _declared_treatment(right)
        ok, ga, gb, ctx, settings = comparison_quotable(a_s, b_s, treatment=declared)
        summary["comparison"] = {
            "pair": pair,
            "quotable": ok,
            "context_hash_check": ctx.render(),
            "settings_comparable_check": settings.render(),
            "treatment": sorted(declared),
            # Rendered whatever `ok` says — computing it for diagnostics is this line's job,
            # and the block below says so. But the *string* now carries the result too.
            # `quotable` sits one key away in the same dict and a line copied out of a report
            # leaves it behind, which is the whole distance between a diagnostic and a quote.
            "mcnemar": _rendered_mcnemar(paired_ex(a_s, b_s), quotable=ok) if a_s.n else None,
            "checks_a": [g.render() for g in ga],
            "checks_b": [g.render() for g in gb],
        }
        if a_s.n and b_s.n:
            # ``mcnemar`` is already set above, on the wider condition — computing it for
            # diagnostics whatever ``quotable`` says is that line's job, not this block's.
            summary["comparison"]["ex_a"] = _measured_dict(headline_ex(a_s))
            summary["comparison"]["ex_b"] = _measured_dict(headline_ex(b_s))
    return summary


def _declared_treatment(config_name: str) -> frozenset[str]:
    """The config's declared treatment from ``configs.toml``, or empty if it has no profile.

    Empty is not a fallback that lets the comparison through — ``settings_comparable`` treats an
    undeclared treatment as ``cannot_evaluate``. An config nobody wrote a profile for is exactly
    that case, and ``KeyError`` is how ``config_profile`` says so.

    **``ValueError`` and ``OSError`` are not caught, and that is a correction.** This used to
    swallow all three, so one typo in ``configs.toml`` — a treatment naming a setting that is not a
    comparability setting, which the loader refuses the whole file for — silently un-declared
    *every* config and turned each comparison into ``cannot_evaluate``. Nothing distinguishes that
    from "these two configs genuinely cannot be compared", so a broken file reads as a data
    problem. A malformed or missing register is a defect in the tree and must say so.
    """
    from governed_bi.registry.config_profiles import config_profile

    try:
        return config_profile(config_name).treatment
    except KeyError:
        return frozenset()


def _with_cross_config_context(
    results: tuple[CheckResult, ...],
    ctx: CheckResult,
) -> tuple[CheckResult, ...]:
    return tuple(ctx if r.field == "context_hash" else r for r in results)


def _rendered_mcnemar(result: Any, *, quotable: bool) -> str:
    """The paired line, with its own quotability in it.

    ``McNemarResult.render`` stays a statement about the statistic and nothing else — it is
    the auditable line, and a check result is not part of the arithmetic. The annotation is
    added here, where the check's answer is known, for the same reason ``Measured`` renders
    *unmeasured* rather than a number: a value that cannot be quoted must not read like one
    in isolation.

    The concrete loss this prevents: ``summary["comparison"]["quotable"]`` is one key away
    and a reader copying the delta out of a report does not bring it. Every real pair in
    ``runs/eval/`` is non-quotable today — ``settings_comparable`` returns ``cannot_evaluate``
    because ~50 of the 51 comparability settings are absent from those rows — so every line this
    renders is currently a diagnostic that looked exactly like a result.
    """
    line = result.render()
    return line if quotable else f"{line}  [NOT QUOTABLE: a check did not pass]"


def _measured_dict(m: Measured[Any]) -> dict[str, Any]:
    if not m.is_measured:
        return {"state": "unmeasured", "why": m.why, "render": m.render()}
    return {"state": "measured", "value": m.value, "render": m.render()}
