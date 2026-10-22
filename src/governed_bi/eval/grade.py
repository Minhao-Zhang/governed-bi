"""Per-row grading against gold results, not SQL strings (ADR 0005).

Order-insensitive unless the question id is order-sensitive. ``correct`` is
the tolerant row comparison when both results are in hand. The fingerprint
matches BIRD's normaliser and is used only when gold is a published digest.
"""

from __future__ import annotations

import hashlib
import json
import math
import numbers
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

__all__ = [
    "GradeResult",
    "result_fingerprint",
    "grade_results",
    "grade_turn",
    "results_match",
    "REL_TOL",
]

#: Relative tolerance for fractional numbers. 1e-9 covers plan and numeric
#: noise; ``1234.568`` against ``1234.567`` stays wrong. Whole numbers compare
#: exactly, so a large count is not absorbed.
REL_TOL = Decimal("1e-9")
#: Floor near zero, where a relative tolerance shrinks to nothing.
ABS_TOL = Decimal("1e-12")


class GradeResult(dict):
    pass


def result_fingerprint(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    order_sensitive: bool = False,
) -> str:
    """sha256 of the normalised rows, column names omitted. Same bytes as BIRD when order is ignored. An order-sensitive
    digest is not BIRD-comparable."""
    body = _normalise(columns, rows, order_sensitive=order_sensitive)
    blob = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def grade_results(
    *,
    pred_columns: Sequence[str],
    pred_rows: Sequence[Sequence[Any]],
    gold_columns: Sequence[str],
    gold_rows: Sequence[Sequence[Any]],
    order_sensitive: bool = False,
) -> GradeResult:
    gold_fp = result_fingerprint(gold_columns, gold_rows, order_sensitive=order_sensitive)
    pred_fp = result_fingerprint(pred_columns, pred_rows, order_sensitive=order_sensitive)
    same = results_match(pred_rows, gold_rows, order_sensitive=order_sensitive)
    return GradeResult(
        correct=same,
        gold_fingerprint=gold_fp,
        pred_fingerprint=pred_fp,
        detail="match" if same else "result_mismatch",
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
    """``correct`` is true, false, or ``None``. A missing gold is ``None``; do not coerce it to ``False``."""
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
        # Both endings score the same: a paused `ask_user` and a fail-closed
        # decline delivered no answer. The split between them is a written
        # field, not this score.
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
    if outcome == "no_sql":
        # `False`, not `None`. `None` means the instrument had no gold. Here the
        # gold is fine and the engine ran no statement.
        return GradeResult(
            correct=False,
            gold_fingerprint=gold_fingerprint,
            pred_fingerprint=None,
            detail="no_sql",
        )
    if outcome != "answered":
        return GradeResult(
            correct=False,
            gold_fingerprint=gold_fingerprint,
            pred_fingerprint=None,
            detail=f"unanswered:{outcome}",
        )

    have_gold_rows = gold_columns is not None and gold_rows is not None
    if gold_fingerprint is None:
        if not have_gold_rows:
            # ``None``, not ``False``: no gold is the instrument, not the model.
            # Recording it as ``False`` is indistinguishable from a real miss
            # and deflates the rate.
            return GradeResult(
                correct=None,
                gold_fingerprint=None,
                pred_fingerprint=None,
                detail="missing_gold",
            )
        gold_fingerprint = result_fingerprint(
            gold_columns, gold_rows, order_sensitive=order_sensitive  # type: ignore[arg-type]
        )

    if pred_columns is None or pred_rows is None:
        return GradeResult(
            correct=False,
            gold_fingerprint=gold_fingerprint,
            pred_fingerprint=None,
            detail="missing_prediction",
        )

    pred_fp = result_fingerprint(pred_columns, pred_rows, order_sensitive=order_sensitive)
    if have_gold_rows:
        same = results_match(pred_rows, gold_rows, order_sensitive=order_sensitive)  # type: ignore[arg-type]
    else:
        same = pred_fp == gold_fingerprint
    return GradeResult(
        correct=same,
        gold_fingerprint=gold_fingerprint,
        pred_fingerprint=pred_fp,
        detail="match" if same else "result_mismatch",
    )


def results_match(
    pred_rows: Sequence[Sequence[Any]],
    gold_rows: Sequence[Sequence[Any]],
    *,
    order_sensitive: bool = False,
) -> bool:
    """Same rows, as a multiset unless order is kept. Text never equals a number. Close values err toward ``False``."""
    if len(pred_rows) != len(gold_rows):
        return False
    pred = [[_typed_cell(v) for v in row] for row in pred_rows]
    gold = [[_typed_cell(v) for v in row] for row in gold_rows]
    if not order_sensitive:
        pred.sort(key=lambda row: tuple(_typed_key(c) for c in row))
        gold.sort(key=lambda row: tuple(_typed_key(c) for c in row))
    for p_row, g_row in zip(pred, gold):
        if len(p_row) != len(g_row):
            return False
        for p, g in zip(p_row, g_row):
            if isinstance(p, (float, Decimal)) and isinstance(g, (float, Decimal)):
                if not _numbers_match(p, g):
                    return False
            elif type(p) is not type(g) or p != g:
                return False
    return True


def _numbers_match(p: float | Decimal, g: float | Decimal) -> bool:
    """Exact for whole ``Decimal`` counts; otherwise within tolerance. A float past 2**53 is not exact."""
    dp, dg = Decimal(p), Decimal(g)
    if isinstance(p, Decimal) and isinstance(g, Decimal) and _is_whole(dp) and _is_whole(dg):
        return dp == dg
    return abs(dp - dg) <= max(REL_TOL * max(abs(dp), abs(dg)), ABS_TOL)


def _is_whole(value: Decimal) -> bool:
    return value == value.to_integral_value()


class _Text(str):
    """Folded text. Its type cannot equal a number's spelling."""


def _typed_cell(value: Any) -> Any:
    """Bool stays apart because ``True == 1.0``. NaN is its own type. A whole float below 2**53 is that int."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, Decimal)):
        exact = Decimal(value)
        return exact if exact.is_finite() else _non_finite(float(exact))
    if isinstance(value, numbers.Number):
        number = float(value)  # type: ignore[arg-type]
        if math.isnan(number) or math.isinf(number):
            return _non_finite(number)
        if number.is_integer() and abs(number) < _EXACT_FLOAT_LIMIT:
            return Decimal(int(number))
        return number
    return _Text(str(value).strip().lower())


#: Every integer up to here is exactly one float. Above it the tolerance holds.
_EXACT_FLOAT_LIMIT = 2**53


class _NonFinite(str):
    """NaN or an infinity, so no text cell can spell one."""


def _non_finite(number: float) -> _NonFinite:
    return _NonFinite("nan" if math.isnan(number) else "inf" if number > 0 else "-inf")


def _typed_key(value: Any) -> tuple[int, float, str]:
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, bool):
        return (1, float(value), "")
    if isinstance(value, (float, Decimal)):
        return (2, float(value), "")
    return (3, 0.0, str(value))


def _normalise(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    order_sensitive: bool,
) -> list[list[Any]]:
    """Column names are not hashed: ``COUNT(*) AS paper_count`` matched the same values and graded wrong."""
    body = [[_coerce_cell(v) for v in row] for row in rows]
    if not order_sensitive:
        body = sorted(body, key=lambda row: tuple(_cell_key(c) for c in row))
    return body


def _coerce_cell(value: Any) -> Any:
    """``float`` first, else folded text. ``Decimal`` is not an int, and ``float`` also equates the string 1e5 with
    100000 and ``True`` with 1."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value).strip().lower()
    if math.isnan(number):
        return "\x00nan"
    if math.isinf(number):
        return "\x00inf" if number > 0 else "\x00-inf"
    return number


def _cell_key(value: Any) -> tuple[int, float, str]:
    """Total order over ``None``, float, str. A lexicographic sort raises on mixed cells."""
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, float):
        return (1, value, "")
    return (2, 0.0, str(value))
