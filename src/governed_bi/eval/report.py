"""Eval report: Populations, McNemar, quotability, cross-arm context_hash gate.

Also the **refusal histogram** — "why did this arm not answer", counted in the vocabulary
``register/stages.py`` declares. ADR 0013 §2 argued for putting the abstention reasons in
``REFUSED_BY_TO_STAGE`` on the grounds that ``classify_outcome``, a refusal histogram and this
module already read that table. Measured on 2026-08-12, all three were false:

* ``classify_outcome`` never consults it. Any truthy ``refused_by`` returns
  ``Outcome.refused``, declared or not.
* The one histogram that exists, ``tools/datalake_report.py::_refusal_layers``, counts
  ``attempt.reason_code`` off the **ledger** and has never touched the table. A withheld turn
  writes no ledger row at all — ADR 0013's own acceptance criterion 3 — so the four abstention
  reasons were not merely uncounted there, they were unreachable.
* This module had zero references to ``refused_by``, ``terminal_reason`` or the vocabulary.

Three named consumers, none of them real: the declared-machinery-with-no-reader shape the
argument itself was invoking. The histogram is built here rather than the sentence weakened, and
:func:`refusal_histogram`'s ``unattributed`` bucket is the part that makes the vocabulary
load-bearing instead of decorative.
"""

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
from governed_bi.register.stages import REFUSED_BY_TO_STAGE, Outcome

__all__ = [
    "CONTEXT_HASH_THRESHOLD",
    "REFUSAL_CHANNELS",
    "arm_population",
    "context_hashes_distinct",
    "evaluate_arm",
    "comparison_quotable",
    "headline_ex",
    "knobs_comparable",
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


def outcome_rates(arm: Population) -> dict[str, Measured[float]]:
    """The ``correct / clarified / refused`` scorecard a benchmark report needs
    (detent-ai-deployment-targets.md) — three named rates over the same population
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


def refusal_histogram(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Why this arm did not answer, counted by reason and by the stage that owns each reason.

    **The point of the ``unattributed`` bucket.** ``REFUSED_BY_TO_STAGE`` is called a closed
    vocabulary, and two import-time guards keep the *declarations* in step with each other — but
    nothing before this read the table against real rows, so a node writing a string that is in
    neither the register nor any guard produced a refusal that every count still absorbed
    silently. Here it lands in its own bucket with its own name, which is what "closed" has to
    mean once artifacts exist: a reader can see that the histogram does not add up and see which
    string is why.

    ``by_stage`` is the question §4.2 of open-work.md asks — *retrieval missed, you may not, or
    the engine decided to withhold* — and it is answerable only because ADR 0012 split
    ``r_table_not_authorized`` out of the licensing count and ADR 0013 put the abstention
    reasons in the same table. Unattributed reasons are **not** in ``by_stage``: there is no
    stage to credit, and inventing one is the misattribution both ADRs exist to end.

    Counted over the rows the arm classified ``refused``. A crash is not a refusal (``Outcome``
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
    """Both arms recorded a ``context_hash`` on the questions they share.

    **An existence check, not a treatment test — that is audit D9's resolution.** It used to
    require ``threshold`` (0.95) of shared questions to have *differing* hashes, on the
    reasoning that a changed treatment changes the context. The inference does not hold in
    that direction: retrieval is nondeterministic, so the hashes differ whether or not the
    treatment did. Measured on ``run1``/``run2``, which differ only by a random seed, it passed
    at **0.9993** (1,350 of 1,351 hashes differ) — and on the 20 other pairs of the seven
    ``proxy_*`` arms in ``runs/eval/`` it never falls below **0.9882**, hitting exactly 1.0000
    on 11 of them (corpus ``30872d3``, recomputed 2026-08-12). It believed it asked "did the
    treatment change" and measured "is there retrieval noise", to which the answer is always
    yes.

    What replaces it is :func:`knobs_comparable`, which reads the declared treatment out of the
    knobs rather than inferring it from a hash. This function keeps the narrower job it can
    actually do: a turn with no ``context_hash`` assembled no context, and a pair of arms where
    that is true of some shared questions cannot be compared on those questions at all.

    ``threshold`` is retained in the signature and deliberately unused by the verdict, because
    callers pass it and a silent behaviour change under an unchanged call is worse than a
    parameter that documents its own retirement. It is reported in the detail line.
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
        comparable, len(shared), what=f"context_hash recorded {a.label!r} vs {b.label!r}"
    )
    return _gate(
        "context_hash",
        Verdict.passed,
        rate,
        a,
        f"both arms assembled a context on all {comparable} shared questions "
        f"({distinct} of them differ; distinctness is no longer the verdict — audit D9 — and "
        f"the retired threshold was {threshold})",
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


def knobs_comparable(
    a: Population,
    b: Population,
    *,
    treatment: frozenset[str] = frozenset(),
) -> GateResult:
    """The arms differ in the declared treatment, and in nothing else that matters.

    Both halves are necessary and the second one is audit D9's fix.

    **Confounders.** Every comparability knob *except* the declared treatment must be recorded
    on both arms and equal. Until 2026-08-11 nothing checked this: ``comparability_keys()``
    and ``config_hash_keys()` had no production caller, so a pair differing in ``chat_model``
    was published as a delta because each arm was internally homogeneous.

    **The treatment itself must have moved.** A comparison whose declared treatment is
    identical on both arms is a replicate wearing an arm's name. This is what D9 asked for:
    treatment difference asserted from *declared fields* rather than inferred from
    ``context_hash`` noise, which measured retrieval nondeterminism and passed at 0.9993 on a
    seed-only null pair.

    ``treatment`` names comparability knobs — a treatment that is not one is a category error
    and refuses rather than being quietly ignored. An **empty** ``treatment`` is not "nothing
    changed", it is "nobody said what changed", and a pair that cannot name its treatment
    cannot be shown to be a comparison rather than a replicate.

    Absent and ``None`` stay apart throughout: ``_resolved_knobs`` flattens ``UNSET`` to
    ``None`` on purpose, so two arms may agree on a recorded ``None``, while a key missing from
    the mapping is the arm declining to say. Collapsing them is how the within-arm gate once
    certified a configuration it never saw.
    """
    keys = comparability_keys()

    undeclared = sorted(treatment - keys)
    if undeclared:
        return _gate(
            "knobs_resolved",
            Verdict.cannot_evaluate,
            Measured.unmeasured("treatment names a knob that is not a comparability knob"),
            a,
            "declared treatment is not in comparability_keys(): " + ", ".join(undeclared),
        )

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

    confounders = keys - treatment
    differing = sorted(k for k in confounders if values_a[k] != values_b[k])
    if differing:
        shown = "; ".join(
            f"{k}: {'/'.join(values_a[k])} vs {'/'.join(values_b[k])}" for k in differing[:4]
        )
        extra = f" (+{len(differing) - 4} more)" if len(differing) > 4 else ""
        return _gate(
            "knobs_resolved",
            Verdict.failed,
            Measured.rate(
                len(confounders) - len(differing),
                len(confounders),
                what="non-treatment comparability knobs agreeing across arms",
            ),
            a,
            f"arms differ on {len(differing)} knob(s) outside the declared treatment: "
            f"{shown}{extra}",
        )

    if not treatment:
        return _gate(
            "knobs_resolved",
            Verdict.cannot_evaluate,
            Measured.unmeasured("no treatment declared"),
            a,
            "no treatment declared, so this pair cannot be shown to be a comparison rather "
            "than a replicate — every comparability knob is identical across the two arms",
        )

    unmoved = sorted(k for k in treatment if values_a[k] == values_b[k])
    if unmoved:
        return _gate(
            "knobs_resolved",
            Verdict.failed,
            Measured.rate(
                len(treatment) - len(unmoved),
                len(treatment),
                what="declared treatment knobs that actually differ",
            ),
            a,
            "the declared treatment is identical on both arms, so this is a replicate and not "
            "a comparison: " + ", ".join(unmoved),
        )

    return _gate(
        "knobs_resolved",
        Verdict.passed,
        Measured.rate(
            len(confounders),
            len(confounders),
            what="non-treatment comparability knobs agreeing across arms",
        ),
        a,
        f"treatment {sorted(treatment)} differs; all {len(confounders)} other comparability "
        "knobs agree",
    )


def comparison_quotable(
    a: Population,
    b: Population,
    *,
    threshold: float = CONTEXT_HASH_THRESHOLD,
    treatment: frozenset[str] = frozenset(),
) -> tuple[bool, tuple[GateResult, ...], tuple[GateResult, ...], GateResult, GateResult]:
    """Whether an arm-to-arm delta may be quoted.

    Two cross-arm gates with different jobs. :func:`context_hashes_distinct` is now an
    **existence** check — both arms assembled a context on the questions they share — because
    audit D9 established that its old 95%-distinctness threshold measured retrieval
    nondeterminism and reported it as a treatment difference, passing at 0.9993 on a pair that
    differs only by a random seed. Judging the treatment is :func:`knobs_comparable`'s job, from
    declared knobs, and ``treatment`` is how the caller names what is supposed to have moved.

    Single-arm ``context_hash`` gates are replaced by the cross-arm one. The ``knobs_resolved``
    gate is **not** replaced: each arm's own within-arm homogeneity is a different question
    from the two arms agreeing with each other, and both must hold.

    Computed **once** and substituted into both arms' rows: each call stamps the population
    of whichever arm it was given first, so recomputing per arm makes arm B's row report arm
    A's ``describe()`` on the provenance line.
    """
    ctx = context_hashes_distinct(a, b, threshold=threshold)
    knobs = knobs_comparable(a, b, treatment=treatment)
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
    treatment: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Build populations, headlines, optional paired McNemar + quotability.

    ``treatment`` names the comparability knobs the pair's second arm was supposed to move.
    Left ``None`` it is read from ``arms.toml`` via the arm's own name, which is the point of
    that file: the claim about what an arm changed is committed and diffable rather than living
    in whoever ran the command. An arm with no profile contributes no treatment, and
    ``knobs_comparable`` then reports ``cannot_evaluate`` rather than guessing — see D9.
    """
    pops = {name: arm_population(rows, label=name) for name, rows in arms.items()}
    summary: dict[str, Any] = {
        "arms": {
            name: {
                "n": pop.n,
                "ex": _measured_dict(headline_ex(pop)),
                "crash_rate": _measured_dict(pop.rate("crashed")),
                # The consumer ADR 0013 §2 named and did not have. Per arm and not per pair:
                # "why did this arm decline" is a description of one arm, and the paired block
                # below is about a difference.
                "refusals": refusal_histogram(pop.rows),
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
        declared = treatment if treatment is not None else _declared_treatment(right)
        ok, ga, gb, ctx, knobs = comparison_quotable(a_s, b_s, treatment=declared)
        summary["comparison"] = {
            "pair": pair,
            "quotable": ok,
            "context_hash_gate": ctx.render(),
            "knobs_comparable_gate": knobs.render(),
            "treatment": sorted(declared),
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


def _declared_treatment(arm_name: str) -> frozenset[str]:
    """The arm's declared treatment from ``arms.toml``, or empty if it has no profile.

    Empty is not a fallback that lets the comparison through — ``knobs_comparable`` treats an
    undeclared treatment as ``cannot_evaluate``. An arm nobody wrote a profile for is exactly
    that case, and ``KeyError`` is how ``arm_profile`` says so.

    **``ValueError`` and ``OSError`` are not caught, and that is a correction.** This used to
    swallow all three, so one typo in ``arms.toml`` — a treatment naming a knob that is not a
    comparability knob, which the loader refuses the whole file for — silently un-declared
    *every* arm and turned each comparison into ``cannot_evaluate``. Nothing distinguishes that
    from "these two arms genuinely cannot be compared", so a broken file reads as a data
    problem. A malformed or missing register is a defect in the tree and must say so.
    """
    from governed_bi.register.arm_profiles import arm_profile

    try:
        return arm_profile(arm_name).treatment
    except KeyError:
        return frozenset()


def _with_cross_arm_context(
    results: tuple[GateResult, ...],
    ctx: GateResult,
) -> tuple[GateResult, ...]:
    return tuple(ctx if r.field == "context_hash" else r for r in results)


def _measured_dict(m: Measured[Any]) -> dict[str, Any]:
    if not m.is_measured:
        return {"state": "unmeasured", "why": m.why, "render": m.render()}
    return {"state": "measured", "value": m.value, "render": m.render()}
