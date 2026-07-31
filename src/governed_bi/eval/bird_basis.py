"""BIRD-basis stage funnel and structural error stats for offline analysis.

Reproduces the mutually exclusive stage waterfall and related tables from
``docs/experiments/20260730T034522Z-curated-sme-error-analysis.md`` over a scored
``generations.*.jsonl`` run.

**Basis.** Drop only ``gold_order_sensitive`` rows (BIRD §9.3). Frozen ``VALUES``
gold stays in the denominator — BIRD grades it.

**Cascade.** First failure wins, in serve order:

1. OK — ``correct``
2. refused — governed refusal (``Outcome.refused``: guardrail / no_coverage). Cap
   exhaustion is *not* refused; those rows continue down the cascade.
3. retrieval — gold schema absent from ``shortlisted_schemas``
4. pick — gold shortlisted but ``routed_hit`` / ``pick_hit`` is false
5. table — delivered SQL does not cover every gold table (or no SQL after a
   correct route, including gold-unusable rows with no ``nrows_match``)
6. wrong_shape — gold tables covered, ``nrows_match is False``
7. wrong_value — gold tables covered, ``nrows_match is True``

This is deliberately *not* :func:`governed_bi.eval.error_taxonomy.attribute_row`:
that cascade uses sql_diff dimensions and treats capped turns as already
attributed, which does not match the report's BIRD-basis table.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Mapping

from ..stages import Outcome, classify_row

__all__ = [
    "FUNNEL_STAGES",
    "bird_basis_rows",
    "funnel_stage",
    "stage_waterfall",
    "schema_pick_report",
    "stage4_structural_report",
    "sme_perturbation_report",
    "decoy_touch_counts",
    "bird_basis_report",
    "question_arm_view",
]

FUNNEL_STAGES: tuple[str, ...] = (
    "OK",
    "retrieval",
    "pick",
    "table",
    "wrong_shape",
    "wrong_value",
    "refused",
)

_DISTINCT_RE = re.compile(r"\bDISTINCT\b", re.IGNORECASE)
_LIKE_RE = re.compile(r"\bLIKE\b", re.IGNORECASE)


def bird_basis_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep every row except order-sensitive gold (BIRD-comparable denominator)."""
    return [dict(r) for r in rows if not r.get("gold_order_sensitive")]


def funnel_stage(row: Mapping[str, Any], gold_sql: Mapping[str, str]) -> str:
    """Assign one mutually exclusive funnel stage to a single row."""
    # Lazy: ``analysis`` imports this module for ``analyse_run``.
    from .analysis import sql_tables

    if row.get("correct"):
        return "OK"
    outcome, _, _ = classify_row(row)
    # Delivery refusals only — capped/exhausted turns fall through so a routed
    # exhaustion is charged to table coverage (no SQL delivered), matching the
    # 20260730 report's refused=guardrail/no_coverage definition.
    if outcome is Outcome.refused:
        return "refused"
    short = row.get("shortlisted_schemas") or []
    db = row.get("db_id")
    if db not in short:
        return "retrieval"
    if row.get("routed_hit") is False or row.get("pick_hit") is False:
        return "pick"
    pred = (row.get("generated_sql") or "").strip()
    if not pred:
        return "table"
    gsql = gold_sql.get(str(row.get("question_id")), "")
    gold_tables = sql_tables(gsql)
    pred_tables = sql_tables(pred)
    if gold_tables and not gold_tables <= pred_tables:
        return "table"
    # No gradeable row counts (e.g. nondeterministic gold): cannot be shape/value.
    if row.get("nrows_match") is None:
        return "table"
    if row.get("nrows_match") is False:
        return "wrong_shape"
    return "wrong_value"


def stage_waterfall(
    rows: Iterable[Mapping[str, Any]], gold_sql: Mapping[str, str]
) -> dict[str, Any]:
    """BIRD-basis stage counts + EX for one arm."""
    basis = bird_basis_rows(rows)
    counts: Counter[str] = Counter(funnel_stage(r, gold_sql) for r in basis)
    n = len(basis)
    ok = counts["OK"]
    return {
        "n": n,
        "ex": (ok / n) if n else None,
        "stages": {stage: counts.get(stage, 0) for stage in FUNNEL_STAGES},
    }


def _picked_schema(row: Mapping[str, Any]) -> str | None:
    routed = row.get("routed_schemas") or []
    if not routed:
        return None
    return str(routed[0])


def _is_pick(row: Mapping[str, Any]) -> bool:
    if row.get("correct"):
        return False
    outcome, _, _ = classify_row(row)
    if outcome is Outcome.refused:
        return False
    short = row.get("shortlisted_schemas") or []
    if row.get("db_id") not in short:
        return False
    return row.get("routed_hit") is False or row.get("pick_hit") is False


def schema_pick_report(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Rank histogram, twin confusions, attractors, and rank-override count.

    Population: BIRD-basis rows whose funnel stage is ``pick`` (gold shortlisted,
    picker chose another). Rank histogram on this population matches the report's
    26/31/39. Twin/attractor *cells* in the error-analysis §3 were counted on a
    broader ``routed_hit=False`` set (and still do not fully reproduce every
    attractor); see the pinned reproduction test for named report-vs-tool diffs.
    """
    basis = bird_basis_rows(rows)
    picks = [r for r in basis if _is_pick(r)]

    rank_hist = {"1": 0, "2": 0, "3+": 0, "none": 0}
    overrides = 0
    pairs: Counter[tuple[str, str]] = Counter()
    attractors: Counter[str] = Counter()
    for r in picks:
        rank = r.get("gold_schema_rank")
        if rank == 1:
            rank_hist["1"] += 1
        elif rank == 2:
            rank_hist["2"] += 1
        elif isinstance(rank, int) and rank >= 3:
            rank_hist["3+"] += 1
        else:
            rank_hist["none"] += 1
        gold_db = str(r.get("db_id") or "")
        picked = _picked_schema(r)
        if picked:
            pairs[(gold_db, picked)] += 1
            attractors[picked] += 1
        short = [str(s) for s in (r.get("shortlisted_schemas") or [])]
        if (
            picked
            and gold_db in short
            and picked in short
            and short.index(picked) > short.index(gold_db)
        ):
            overrides += 1

    twin_pairs = [
        {
            "gold": g,
            "picked": p,
            "n": n,
            "symmetric_n": pairs.get((p, g), 0),
        }
        for (g, p), n in pairs.most_common()
    ]
    return {
        "n_pick_wrong_gold_shortlisted": len(picks),
        "gold_rank_histogram": rank_hist,
        "rank_overrides": overrides,
        "twin_pairs": twin_pairs[:20],
        "attractors": [
            {"schema": s, "n": n} for s, n in attractors.most_common(15)
        ],
    }


def stage4_structural_report(
    rows: Iterable[Mapping[str, Any]], gold_sql: Mapping[str, str]
) -> dict[str, Any]:
    """DISTINCT / LIKE / over-join incidence on funnel stage-4 misses.

    ``extra_distinct`` / ``missing_distinct`` use a case-insensitive ``\\bDISTINCT\\b``
    scan (not only ``SELECT DISTINCT``), matching the report's "spurious DISTINCT"
    story that includes ``COUNT(DISTINCT ...)``. Over-join is any predicted table
    absent from gold (``pred - gold`` nonempty), not requiring a proper superset.
    """
    from .analysis import sql_tables

    basis = bird_basis_rows(rows)
    stage4 = [
        r
        for r in basis
        if funnel_stage(r, gold_sql) in ("wrong_shape", "wrong_value")
    ]
    extra_distinct = missing_distinct = like = over_join = 0
    for r in stage4:
        gsql = gold_sql.get(str(r.get("question_id")), "")
        pred = r.get("generated_sql") or ""
        gd = bool(_DISTINCT_RE.search(gsql))
        pd = bool(_DISTINCT_RE.search(pred))
        if pd and not gd:
            extra_distinct += 1
        if gd and not pd:
            missing_distinct += 1
        if _LIKE_RE.search(pred) and not _LIKE_RE.search(gsql):
            like += 1
        gold_tables = set(sql_tables(gsql))
        pred_tables = set(sql_tables(pred))
        if pred_tables - gold_tables:
            over_join += 1
    return {
        "n_stage4": len(stage4),
        "extra_distinct": extra_distinct,
        "missing_distinct": missing_distinct,
        "like_vs_exact": like,
        "over_join": over_join,
    }


def sme_perturbation_report(
    curated: Iterable[Mapping[str, Any]],
    curated_sme: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """SQL rewrite / flip counts between curated and curated_sme on BIRD basis."""
    c = {str(r.get("question_id")): r for r in bird_basis_rows(curated)}
    s = {str(r.get("question_id")): r for r in bird_basis_rows(curated_sme)}
    common = sorted(set(c) & set(s))
    sql_changed = 0
    helped = hurt = 0
    for qid in common:
        csql = (c[qid].get("generated_sql") or "").strip()
        ssql = (s[qid].get("generated_sql") or "").strip()
        if csql != ssql:
            sql_changed += 1
        c_ok = bool(c[qid].get("correct"))
        s_ok = bool(s[qid].get("correct"))
        if s_ok and not c_ok:
            helped += 1
        if c_ok and not s_ok:
            hurt += 1
    return {
        "n": len(common),
        "sql_changed": sql_changed,
        "sql_changed_rate": (sql_changed / len(common)) if common else None,
        "helped": helped,
        "hurt": hurt,
        "net": helped - hurt,
    }


def decoy_touch_counts(
    arms: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, int]:
    """Raw ``decoy_touch`` counts per arm (full arm, not BIRD-trimmed)."""
    return {
        arm: sum(1 for r in rows if r.get("decoy_touch"))
        for arm, rows in arms.items()
    }


def bird_basis_report(
    arms: Mapping[str, list[dict[str, Any]]],
    gold_sql: Mapping[str, str],
    *,
    pick_arm: str = "curated_sme",
) -> dict[str, Any]:
    """Full BIRD-basis block for ``analyse_run`` / ``analysis.json``."""
    waterfall = {
        arm: stage_waterfall(rows, gold_sql) for arm, rows in sorted(arms.items())
    }
    out: dict[str, Any] = {
        "exclusion": "gold_order_sensitive only; frozen VALUES gold kept",
        "waterfall": waterfall,
        "decoy_touch": decoy_touch_counts(arms),
    }
    if pick_arm in arms:
        out["schema_pick"] = {
            "arm": pick_arm,
            **schema_pick_report(arms[pick_arm]),
        }
        out["stage4_sql"] = {
            "arm": pick_arm,
            **stage4_structural_report(arms[pick_arm], gold_sql),
        }
    if "curated" in arms and "curated_sme" in arms:
        out["sme_perturbation"] = sme_perturbation_report(
            arms["curated"], arms["curated_sme"]
        )
    return out


def question_arm_view(
    arms: Mapping[str, list[dict[str, Any]]],
    question_id: str,
    gold_sql: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Cross-arm debug view for one ``question_id``.

    Shows each arm's SQL, correctness, funnel stage, and routing fields — not
    just McNemar discordance counts.
    """
    gold_sql = gold_sql or {}
    per_arm: dict[str, Any] = {}
    for arm, rows in sorted(arms.items()):
        matches = [
            r
            for r in rows
            if str(r.get("question_id") or r.get("request_id") or "") == question_id
        ]
        if not matches:
            per_arm[arm] = None
            continue
        r = matches[0]
        per_arm[arm] = {
            "db_id": r.get("db_id"),
            "correct": bool(r.get("correct")),
            "funnel_stage": (
                funnel_stage(r, gold_sql)
                if not r.get("gold_order_sensitive")
                else "excluded_order_sensitive"
            ),
            "generated_sql": r.get("generated_sql"),
            "pred_nrows": r.get("pred_nrows"),
            "gold_nrows": r.get("gold_nrows"),
            "nrows_match": r.get("nrows_match"),
            "routed_hit": r.get("routed_hit"),
            "pick_hit": r.get("pick_hit"),
            "routed_schemas": r.get("routed_schemas"),
            "shortlisted_schemas": r.get("shortlisted_schemas"),
            "gold_schema_rank": r.get("gold_schema_rank"),
            "refused_by": r.get("refused_by"),
            "error": r.get("error"),
            "outcome": classify_row(r)[0].value,
        }
    return {
        "question_id": question_id,
        "gold_sql": gold_sql.get(question_id),
        "arms": per_arm,
    }
