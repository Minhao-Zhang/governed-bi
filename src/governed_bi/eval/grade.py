"""Per-row EX grading against gold **results** (ADR 0005 §4.1).

Compares executed result sets, not SQL strings. Order-insensitive by default;
order-sensitive question ids keep row order.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "GradeResult",
    "result_fingerprint",
    "grade_results",
    "grade_turn",
]


class GradeResult(dict):
    """``correct``, ``gold_fingerprint``, ``pred_fingerprint``, ``detail``."""


def result_fingerprint(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    order_sensitive: bool = False,
) -> str:
    """Canonical sha256 of a result set for EX comparison."""
    normalised = _normalise(columns, rows, order_sensitive=order_sensitive)
    blob = json.dumps(normalised, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def grade_results(
    *,
    pred_columns: Sequence[str],
    pred_rows: Sequence[Sequence[Any]],
    gold_columns: Sequence[str],
    gold_rows: Sequence[Sequence[Any]],
    order_sensitive: bool = False,
) -> GradeResult:
    """EX: fingerprints of predicted vs gold result sets match."""
    gold_fp = result_fingerprint(gold_columns, gold_rows, order_sensitive=order_sensitive)
    pred_fp = result_fingerprint(pred_columns, pred_rows, order_sensitive=order_sensitive)
    return GradeResult(
        correct=gold_fp == pred_fp,
        gold_fingerprint=gold_fp,
        pred_fingerprint=pred_fp,
        detail="match" if gold_fp == pred_fp else "result_mismatch",
    )


def grade_turn(
    *,
    outcome: str | None,
    pred_columns: Sequence[str] | None = None,
    pred_rows: Sequence[Sequence[Any]] | None = None,
    gold_columns: Sequence[str] | None = None,
    gold_rows: Sequence[Sequence[Any]] | None = None,
    gold_fingerprint: str | None = None,
    order_sensitive: bool = False,
) -> GradeResult:
    """Grade one serve turn.

    Crashes and refusals are **incorrect**, never collapsed into each other —
    ``correct=False`` with ``detail`` naming the outcome. Only ``answered`` turns
    with a comparable result can be ``correct=True``.
    """
    if outcome == "crashed":
        return GradeResult(
            correct=False,
            gold_fingerprint=gold_fingerprint,
            pred_fingerprint=None,
            detail="crashed",
        )
    if outcome == "refused":
        return GradeResult(
            correct=False,
            gold_fingerprint=gold_fingerprint,
            pred_fingerprint=None,
            detail="refused",
        )
    if outcome == "clarification":
        return GradeResult(
            correct=False,
            gold_fingerprint=gold_fingerprint,
            pred_fingerprint=None,
            detail="clarification",
        )
    if outcome == "capped":
        return GradeResult(
            correct=False,
            gold_fingerprint=gold_fingerprint,
            pred_fingerprint=None,
            detail="capped",
        )
    if outcome != "answered":
        return GradeResult(
            correct=False,
            gold_fingerprint=gold_fingerprint,
            pred_fingerprint=None,
            detail=f"unanswered:{outcome}",
        )

    if gold_fingerprint is None:
        if gold_columns is None or gold_rows is None:
            return GradeResult(
                correct=False,
                gold_fingerprint=None,
                pred_fingerprint=None,
                detail="missing_gold",
            )
        gold_fingerprint = result_fingerprint(
            gold_columns, gold_rows, order_sensitive=order_sensitive
        )

    if pred_columns is None or pred_rows is None:
        return GradeResult(
            correct=False,
            gold_fingerprint=gold_fingerprint,
            pred_fingerprint=None,
            detail="missing_prediction",
        )

    pred_fp = result_fingerprint(pred_columns, pred_rows, order_sensitive=order_sensitive)
    return GradeResult(
        correct=pred_fp == gold_fingerprint,
        gold_fingerprint=gold_fingerprint,
        pred_fingerprint=pred_fp,
        detail="match" if pred_fp == gold_fingerprint else "result_mismatch",
    )


def _normalise(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    order_sensitive: bool,
) -> dict[str, Any]:
    cols = [str(c) for c in columns]
    body = [[_cell(v) for v in row] for row in rows]
    if not order_sensitive:
        body = sorted(body, key=lambda r: json.dumps(r, separators=(",", ":")))
    return {"columns": cols, "rows": body}


def _cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:  # NaN
            return "NaN"
        return value
    if isinstance(value, Mapping):
        return {str(k): _cell(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_cell(v) for v in value]
    return str(value)
