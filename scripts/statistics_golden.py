"""Byte-for-byte golden for the eval statistics cluster (M4b N19).

Why this exists
---------------
N19 moves ~1.5k lines of aggregation out of ``eval/run_datalake.py`` into
``eval/statistics.py``. That move is supposed to be **pure carriage**: not one
statistic may change. The checklist proposed verifying it against "X.5.4's nine
baseline numbers", but X.5.4 was never run and those baselines do not exist.

So this is the net instead, and it is a stronger one: replay a real 1351-question
x 4-arm run through the three big entry points, serialise everything they return,
and require the bytes to match across the move. Offline — no model, no Postgres,
no BIRD checkout. It reads only ``generations.*.jsonl`` from a finished run.

Build the golden **before** touching production code. Regenerating it afterwards
would prove nothing: it would be the moved code certifying itself.

Usage::

    # before the move
    uv run python scripts/statistics_golden.py \
        --run-dir runs/datalake/20260730T034522Z-test-ladder-fixed2/20260730T034543Z \
        --out /tmp/golden.before.json

    # after the move — same command, then:
    diff /tmp/golden.before.json /tmp/golden.after.json

The script resolves the statistics entry points by name from whichever module
currently owns them, so the *same file* runs on both sides of the move without
edits. That is deliberate: a golden you have to edit between the two runs is not
a golden.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

# --------------------------------------------------------------------------
# Entry-point resolution
# --------------------------------------------------------------------------
# Each statistic names an ordered list of ``(module, attribute)`` candidates:
# the post-move home first, the pre-move one second. The first that resolves
# wins, which is what lets one unmodified file produce both the "before" and
# the "after".
#
# The pairs are spelled out per module rather than "try these names in both
# modules" because one name collides: ``run_datalake`` already imports a
# *different* ``compare_arms`` from ``.treatment`` (per-pair treatment
# divergence, not paired scoring). A name-only search would silently bind the
# wrong function and the golden would compare two different things.
_LOOKUP: dict[str, tuple[tuple[str, str], ...]] = {
    "summarise_rows": (("statistics", "summarise_rows"), ("run_datalake", "_summarise_rows")),
    "compare_arms": (("statistics", "compare_arms"), ("run_datalake", "_compare_arms")),
    "ladder_deltas": (("statistics", "ladder_deltas"), ("run_datalake", "ladder_deltas")),
    "routing_escaped": (("statistics", "routing_escaped"), ("run_datalake", "_routing_escaped")),
    "bool_rate": (("statistics", "_bool_rate"), ("run_datalake", "_bool_rate")),
    "fmt_rate": (("statistics", "fmt_rate"), ("run_datalake", "_fmt_rate")),
    "mean": (("statistics", "_mean"), ("run_datalake", "_mean")),
    "rate_over": (("statistics", "_rate_over"), ("run_datalake", "_rate_over")),
    "price_verdict": (("statistics", "price_verdict"), ("run_datalake", "price_verdict")),
}


def _resolve() -> dict[str, Callable[..., Any]]:
    from governed_bi.eval import run_datalake

    mods: dict[str, Any] = {"run_datalake": run_datalake}
    try:  # only exists after the move
        from governed_bi.eval import statistics as stats_mod

        mods["statistics"] = stats_mod
    except ImportError:
        pass

    found: dict[str, Callable[..., Any]] = {}
    for key, candidates in _LOOKUP.items():
        for mod_name, attr in candidates:
            mod = mods.get(mod_name)
            if mod is None:
                continue
            fn = getattr(mod, attr, None)
            if fn is not None:
                found[key] = fn
                break
        if key not in found:
            raise SystemExit(
                f"cannot resolve statistic {key!r} under any of {candidates}; available modules: {sorted(mods)}"
            )
    return found


# --------------------------------------------------------------------------
# Deterministic serialisation
# --------------------------------------------------------------------------
def _plain(obj: Any) -> Any:
    """Coerce to something ``json.dumps`` renders identically every run.

    Sets are sorted (their iteration order is not stable across processes),
    tuples become lists, and anything else exotic becomes its ``repr``. Floats
    are left alone: CPython's float repr is exact and stable, which is the whole
    point of comparing bytes.
    """
    if isinstance(obj, (set, frozenset)):
        return sorted(_plain(x) for x in obj)
    if isinstance(obj, tuple):
        return [_plain(x) for x in obj]
    if isinstance(obj, list):
        return [_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return repr(obj)


def _dump(obj: Any) -> str:
    return json.dumps(_plain(obj), sort_keys=True, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _pseudo_gold(rows_by_arm: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    """A gold-SQL map derived from the run itself, so no BIRD checkout is needed.

    ``_summarise_rows`` only *reads* gold to drive ``free_pass_counts`` and
    ``attribute_rows``; it never grades against it (grading already happened,
    row by row, during the run). Any deterministic map therefore exercises those
    branches, and one built from the run's own delivered SQL is deterministic,
    self-contained, and shaped like the real thing. It is not the real gold and
    is not claimed to be — this file certifies "the move changed nothing", not
    "the numbers are correct".
    """
    gold: dict[str, str] = {}
    for arm in sorted(rows_by_arm):
        for row in rows_by_arm[arm]:
            qid = str(row.get("question_id") or "")
            if qid and qid not in gold and row.get("generated_sql"):
                gold[qid] = str(row["generated_sql"])
    return gold


def _routing_cases(
    rows_by_arm: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Every distinct routing-escape input the run actually produced, plus edges.

    ``routing_escaped`` is a pure predicate over four fields, so replaying the
    run's distinct tuples covers it far better than hand-written cases would —
    and the hand-written edges below cover the shapes the run happened not to hit.
    """
    seen: set[tuple[Any, ...]] = set()
    cases: list[dict[str, Any]] = []
    for arm in sorted(rows_by_arm):
        for row in rows_by_arm[arm]:
            tables = row.get("tables_used") or []
            used = sorted({str(t).split(".", 1)[0] for t in tables if "." in str(t)})
            routed = sorted(str(s) for s in (row.get("routed_schemas") or []))
            bypassed = bool(row.get("routing_bypassed"))
            unresolved = sorted(str(s) for s in (row.get("tables_used_unresolved") or []))
            key = (tuple(used), tuple(routed), bypassed, tuple(unresolved))
            if key in seen:
                continue
            seen.add(key)
            cases.append(
                {
                    "used_schemas": used,
                    "routed": routed,
                    "bypassed": bypassed,
                    "unresolved_ids": unresolved,
                }
            )
    cases.sort(key=lambda c: json.dumps(c, sort_keys=True))
    # Shapes the run may not have produced. ``used_schemas=None`` in particular
    # is the "could not resolve any table" path and must keep returning None.
    cases.extend(
        [
            {"used_schemas": None, "routed": [], "bypassed": False, "unresolved_ids": []},
            {"used_schemas": None, "routed": ["a"], "bypassed": True, "unresolved_ids": ["x"]},
            {"used_schemas": [], "routed": [], "bypassed": False, "unresolved_ids": []},
            {"used_schemas": ["a"], "routed": [], "bypassed": True, "unresolved_ids": []},
            {"used_schemas": ["a"], "routed": ["a"], "bypassed": False, "unresolved_ids": []},
            {"used_schemas": ["a"], "routed": ["b"], "bypassed": False, "unresolved_ids": []},
            {"used_schemas": ["a", "b"], "routed": ["a"], "bypassed": False, "unresolved_ids": []},
            {"used_schemas": ["a"], "routed": ["a"], "bypassed": False, "unresolved_ids": ["q1"]},
            # Definitive escape *despite* unresolved ids: a resolved schema is
            # already outside the routed set, so the unknown ids cannot rescue it.
            {"used_schemas": ["a", "b"], "routed": ["a"], "bypassed": False, "unresolved_ids": ["q1"]},
            # Unresolved-only, nothing resolved: unknown, not compliant.
            {"used_schemas": [], "routed": ["a"], "bypassed": False, "unresolved_ids": ["q1"]},
            {"used_schemas": None, "routed": ["a"], "bypassed": False, "unresolved_ids": ["q1"]},
            # Bypassed dominates every other signal.
            {"used_schemas": ["b"], "routed": ["a"], "bypassed": True, "unresolved_ids": ["q1"]},
        ]
    )
    return cases


# --------------------------------------------------------------------------
# The golden itself
# --------------------------------------------------------------------------
#: Numeric row fields fed to ``mean``, and boolean ones fed to ``bool_rate``.
#: Chosen to include fields that are absent from some rows, because "absent is
#: not zero" is the invariant those two helpers exist to hold.
_MEAN_KEYS = (
    "latency_sec",
    "cost_est_usd",
    "attempts",
    "n_tool_calls",
    "context_chars",
    "token_sum",
    "ledger_len",
    "n_notes_injected",
    "gold_schema_rank",
    "no_such_key_anywhere",
)
_BOOL_KEYS = (
    "correct",
    "correct_strict",
    "routed_hit",
    "pick_hit",
    "decoy_touch",
    "routing_escaped",
    "gold_twin_in_train",
    "semantic_assurance",
    "no_such_key_anywhere",
)


def build_golden(run_dir: Path) -> tuple[dict[str, Any], dict[str, str]]:
    fns = _resolve()
    summarise = fns["summarise_rows"]
    compare = fns["compare_arms"]
    deltas_fn = fns["ladder_deltas"]

    paths = sorted(run_dir.glob("generations.*.jsonl"))
    if not paths:
        raise SystemExit(f"no generations.*.jsonl under {run_dir}")
    rows_by_arm = {p.name.split(".")[1]: _read_rows(p) for p in paths}

    out: dict[str, Any] = {
        "_input": {
            "run_dir": run_dir.name,
            "arms": sorted(rows_by_arm),
            "n_rows": {a: len(r) for a, r in sorted(rows_by_arm.items())},
        },
    }
    # Where each statistic came from is *reported*, never serialised: it is the
    # one thing that legitimately differs before and after the move, and a golden
    # that embeds it could never be byte-identical. Printed to stdout instead, so
    # the "after" run visibly proves it exercised ``eval.statistics``.
    out.setdefault("_input", {})
    resolved = {k: f"{v.__module__}.{v.__name__}" for k, v in sorted(fns.items())}

    gold = _pseudo_gold(rows_by_arm)
    out["_input"]["n_gold"] = len(gold)

    # --- summarise_rows: four call shapes per arm -------------------------
    summaries: dict[str, Any] = {}
    per_arm: dict[str, Any] = {}
    for arm in sorted(rows_by_arm):
        rows = rows_by_arm[arm]
        plain = summarise(arm, rows)
        with_gold = summarise(arm, rows, gold=gold, corpus_note_assets=len(rows))
        nested = summarise(arm, rows, gold=gold, nested=True)
        empty = summarise(arm, [])
        per_arm[arm] = {
            "plain": plain,
            "with_gold": with_gold,
            "nested": nested,
            "empty": empty,
        }
        # ladder_deltas is fed the shape run_datalake feeds it.
        summaries[arm] = with_gold
    out["summarise_rows"] = per_arm

    # --- ladder_deltas: with and without the paired rows ------------------
    out["ladder_deltas"] = {
        "with_rows": deltas_fn(summaries, rows_by_arm=rows_by_arm),
        "without_rows": deltas_fn(summaries),
        "empty": deltas_fn({}),
    }

    # --- compare_arms: no replicate, and each arm as the replicate --------
    comparisons, divergences = compare(rows_by_arm)
    compared: dict[str, Any] = {
        "no_replicate": {"comparisons": comparisons, "divergences": divergences},
    }
    for arm in sorted(rows_by_arm):
        c, d = compare(rows_by_arm, replicate_of=arm)
        compared[f"replicate_of={arm}"] = {"comparisons": c, "divergences": d}
    out["compare_arms"] = compared

    # --- routing_escaped --------------------------------------------------
    escaped = fns["routing_escaped"]
    out["routing_escaped"] = [
        {
            "in": case,
            "out": escaped(
                set(case["used_schemas"]) if case["used_schemas"] is not None else None,
                list(case["routed"]),
                bypassed=case["bypassed"],
                unresolved_ids=list(case["unresolved_ids"]),
            ),
        }
        for case in _routing_cases(rows_by_arm)
    ]

    # --- the small helpers ------------------------------------------------
    mean = fns["mean"]
    bool_rate = fns["bool_rate"]
    rate_over = fns["rate_over"]
    fmt_rate = fns["fmt_rate"]
    helpers: dict[str, Any] = {"mean": {}, "bool_rate": {}, "rate_over": {}}
    for arm in sorted(rows_by_arm):
        rows = rows_by_arm[arm]
        helpers["mean"][arm] = {k: mean(rows, k) for k in _MEAN_KEYS}
        helpers["bool_rate"][arm] = {k: bool_rate(rows, k) for k in _BOOL_KEYS}
        helpers["rate_over"][arm] = {
            "all": rate_over(rows),
            "gradeable": rate_over([r for r in rows if r.get("error") is None]),
            "empty": rate_over([]),
            "refused": rate_over([r for r in rows if r.get("refused_by")]),
        }
    helpers["mean"]["_empty"] = {k: mean([], k) for k in _MEAN_KEYS}
    helpers["bool_rate"]["_empty"] = {k: bool_rate([], k) for k in _BOOL_KEYS}
    helpers["fmt_rate"] = [
        [fmt_rate(v), fmt_rate(v, 1), fmt_rate(v, 6)]
        for v in (None, 0.0, 1.0, 0.5, 1 / 3, 2 / 3, 0.0005, -1.25, 1e-9, 123456.789)
    ]
    out["helpers"] = helpers

    # --- price_verdict ----------------------------------------------------
    # Exercised through ladder_deltas above, but pinned directly too: it is 140
    # lines of branching that only a handful of ladder steps ever reach.
    verdict = fns["price_verdict"]
    ids_a = frozenset({"q1", "q2", "q3"})
    ids_b = frozenset({"q1", "q2", "q9"})
    grid: list[dict[str, Any]] = []
    for n_lo, n_hi in ((None, 3), (3, None), (3, 3), (3, 4), (0, 0)):
        for lo_cost, hi_cost in ((None, 1.0), (1.0, None), (0.0, 0.0), (1.0, 3.5)):
            for lo_priced, hi_priced in ((None, 3), (3, 3), (1, 3), (0, 0)):
                for added in (None, -2, 0, 5):
                    for ids in ((None, None), (ids_a, ids_a), (ids_a, ids_b)):
                        case = {
                            "lo": "curated",
                            "hi": "curated_sme",
                            "n_lo": n_lo,
                            "n_hi": n_hi,
                            "lo_cost": lo_cost,
                            "hi_cost": hi_cost,
                            "lo_priced": lo_priced,
                            "hi_priced": hi_priced,
                            "added": added,
                            "ids_lo": ids[0],
                            "ids_hi": ids[1],
                        }
                        grid.append({"in": case, "out": verdict(**case)})
    out["price_verdict"] = grid

    return out, resolved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="a finished run directory containing generations.<arm>.jsonl",
    )
    ap.add_argument("--out", type=Path, required=True, help="where to write the golden")
    args = ap.parse_args(argv)

    run_dir: Path = args.run_dir
    if not run_dir.is_dir():
        print(f"run dir not found: {run_dir}", file=sys.stderr)
        return 2

    golden, resolved = build_golden(run_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(_dump(golden) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes)")
    for k, v in resolved.items():
        print(f"  {k:18s} <- {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
