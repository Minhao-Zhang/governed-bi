"""Train-vs-test gap: the overfitting measure for a corpus-as-treatment run.

Scoring the train split is not a second result. The curator reads train gold SQL —
:func:`governed_bi.curator.seed.seed_from_train_sql` extracts joins and metrics from
it, and ``_mark_columns_absent_from_gold`` derives the decoy mask from it — so a
curated arm's train EX is partly recall of statements it was built from.
:func:`governed_bi.eval.index.quotable` already refuses a train-scored run for
exactly this reason ("a diagnostic, not a result").

What the pair buys is the **gap**. ``ex(train) - ex(test)`` per arm is how much of an
arm's score does not survive being asked something new, and it is the one number
that separates the two readings of a positive curated delta:

- a *small* gap says the corpus encodes something reusable;
- a *large* gap says it encodes the training statements.

Both splits must be scored against the **same** corpora. The curator is stochastic,
so a rebuild between splits mixes overfitting with curator variance and the gap stops
meaning either — which is why ``run_datalake`` takes ``corpus_dir`` separately from
``out_dir``.

The gap is a within-arm quantity and needs no shared question ids, so unlike the
ladder deltas it is not paired and carries no p-value. It is a diagnostic; treat a
sign as informative and a magnitude as approximate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Rates worth gapping. Every one is an accuracy-like quantity where "train is
#: higher" means "did not transfer". Deliberately not every rate in the summary:
#: gapping ``crash_rate`` or ``refusal_rate`` invites reading operational noise as
#: overfitting.
GAPPED_RATES: tuple[str, ...] = (
    "ex_lenient",
    "ex_strict",
    "ex_gradeable",
    "conditional_ex_lenient",
    "cond_ex_given_routing",
    "routing_recall",
    "schema_pick_accuracy",
)


def _arms_block(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    arms = summary.get("arms")
    return arms if isinstance(arms, dict) else {}


def _gap(train: Any, test: Any) -> float | None:
    """``train - test``, or ``None`` unless both sides were measured.

    ``None`` on either side is not zero: an arm that measured nothing on one split
    has no gap, and rendering that as 0.0 reads as "transferred perfectly".
    """
    if not isinstance(train, (int, float)) or not isinstance(test, (int, float)):
        return None
    if isinstance(train, bool) or isinstance(test, bool):
        return None
    return float(train) - float(test)


def split_gap(
    train_summary: dict[str, Any], test_summary: dict[str, Any]
) -> dict[str, Any]:
    """Per-arm train-minus-test gaps, plus what could not be compared.

    Arms are intersected rather than unioned: a gap needs both sides, and an arm
    present on one split only is reported in ``arms_not_in_both`` instead of being
    silently dropped.
    """
    train_arms, test_arms = _arms_block(train_summary), _arms_block(test_summary)
    shared = sorted(set(train_arms) & set(test_arms))
    only = sorted(set(train_arms) ^ set(test_arms))

    by_arm: dict[str, dict[str, Any]] = {}
    for arm in shared:
        tr, te = train_arms[arm], test_arms[arm]
        gaps = {
            rate: _gap(tr.get(rate), te.get(rate))
            for rate in GAPPED_RATES
            if rate in tr or rate in te
        }
        by_arm[arm] = {
            "n_train": tr.get("n"),
            "n_test": te.get("n"),
            "train": {r: tr.get(r) for r in GAPPED_RATES if r in tr},
            "test": {r: te.get(r) for r in GAPPED_RATES if r in te},
            "gap": gaps,
        }

    return {
        "reading": (
            "gap = train - test, per arm. Positive means the arm scored higher on the "
            "questions the curator was built from than on held-out ones, i.e. that "
            "much of its score did not transfer. Not paired and not significance "
            "tested: a within-arm diagnostic, never a headline. The train split is "
            "never quotable as performance (eval.index.quotable refuses it)."
        ),
        "arms": by_arm,
        "arms_not_in_both": only,
    }


def write_split_gap(base_dir: Path, train_dir: Path, test_dir: Path) -> dict[str, Any]:
    """Read both summaries, write ``split_gap.json`` under ``base_dir``, return it.

    Returns a ``{"error": ...}`` block rather than raising if either summary is
    missing: the two scored splits are already on disk at this point, and losing them
    to a reporting fault would be the expensive failure.
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any]
    try:
        train = json.loads((train_dir / "summary.json").read_text(encoding="utf-8"))
        test = json.loads((test_dir / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        out = {"error": f"{type(err).__name__}: {err}"}
    else:
        out = split_gap(train, test)
        out["train_dir"] = str(train_dir).replace("\\", "/")
        out["test_dir"] = str(test_dir).replace("\\", "/")

    (base_dir / "split_gap.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return out


def format_split_gap(report: dict[str, Any]) -> str:
    """One line per arm for stdout. A report nobody prints is a report nobody reads."""
    if "error" in report:
        return f"  split gap unavailable: {report['error']}"
    lines: list[str] = []
    for arm, block in report.get("arms", {}).items():
        ex_gap = block["gap"].get("ex_lenient")
        tr = block["train"].get("ex_lenient")
        te = block["test"].get("ex_lenient")

        def _f(v: Any) -> str:
            return "n/a" if not isinstance(v, (int, float)) else f"{v:.3f}"

        lines.append(
            f"  [{arm}] EX train={_f(tr)}(n={block['n_train']}) "
            f"test={_f(te)}(n={block['n_test']})  gap={_f(ex_gap)}"
        )
    if report.get("arms_not_in_both"):
        lines.append(f"  not in both splits: {report['arms_not_in_both']}")
    return "\n".join(lines) or "  no arm scored on both splits"
