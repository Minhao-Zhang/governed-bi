"""Per-row EX grading against gold **results** (ADR 0005 §4.1).

Compares executed result sets, not SQL strings. Order-insensitive by default;
order-sensitive question ids keep row order.

**The comparison is BIRD-Obfuscation's ``normalise_result``, transcribed** — the same
function, so a fingerprint produced here and one from ``pipeline/_db.py``'s
``hash_normalised_result`` are the same 64 hex characters for the same rows. Why it had to
be transcribed rather than approximated is in :func:`_coerce_cell`.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
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
    """Canonical sha256 of a result set for EX comparison.

    Byte-identical to ``hash_normalised_result`` in BIRD-Obfuscation's ``pipeline/_db.py``
    when ``order_sensitive`` is false — same normaliser, same
    ``json.dumps(..., separators=(",", ":"), ensure_ascii=False)``, same digest. That is
    what lets a ``gold_fingerprint`` from the benchmark's own pipeline be compared here
    instead of re-executing every gold statement.

    ``columns`` is accepted and **not hashed**; see :func:`_normalise`.

    ``order_sensitive=True`` skips the sort and is therefore *stricter* than BIRD, whose
    comparators always sort. Deliberate — the dataset ships ``order_sensitive_qids.json``
    — but those fingerprints are not BIRD-comparable, so a benchmark-pipeline gold
    fingerprint must not be used for those question ids.
    """
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
    """Grade one serve turn. ``correct`` is ``True``, ``False``, or ``None`` for *unmeasured*.

    Crashes, refusals, caps and statement-less turns are **incorrect**, never collapsed into
    each other — ``correct=False`` with ``detail`` naming the outcome. Only ``answered`` turns
    with a comparable result can be ``correct=True``.

    **Three values, not two.** A missing *gold* is ``None`` — the instrument had nothing to
    compare against — while a missing *prediction* stays ``False``, because the model
    produced SQL that would not execute. Callers must propagate the ``None`` rather than
    coerce it; ``bool(None)`` is ``False``, the collapse
    :meth:`~governed_bi.measure.population.Population.count` refuses downstream.
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
    if outcome == "no_sql":
        # `False`, like every other non-answer, and **not** `None`: `None` is reserved for the
        # instrument having nothing to compare against, and here the gold is fine — the engine
        # ran no statement. The EX arithmetic is unchanged by the taxonomy split, deliberately:
        # before it, these turns arrived as `answered` with no `generated_sql`, so the harness
        # executed nothing, `pred_columns` stayed `None` and `grade_turn` returned
        # `correct=False, detail="missing_prediction"`. Same score, and `detail` now says which
        # of the two it was rather than describing a prediction that was never attempted.
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

    if gold_fingerprint is None:
        if gold_columns is None or gold_rows is None:
            # ``None``, not ``False``: no gold is the instrument failing, not the model. A gold
            # that will not execute (connection blip, moved schema) recorded as ``False`` is
            # indistinguishable in the artifact from a real mistake and deflates EX silently —
            # ``Population.count``'s "absent is not negative" rule, one layer upstream of it.
            return GradeResult(
                correct=None,
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
) -> list[list[Any]]:
    """``normalise_result`` from BIRD-Obfuscation's ``pipeline/_db.py``, as a list of lists.

    **Column names are deliberately not part of the fingerprint.** BIRD's EX compares result
    *values*, so hashing names made this grader stricter than the benchmark it implements:
    ``SELECT COUNT(*) AS paper_count`` graded wrong against a gold ``SELECT COUNT(*)``
    returning the same ``100``. How often that happened was counted through the old grader and
    is retired (``register/citations.py``); the mismatch itself is mechanical.

    ``columns`` stays in the signature because the *count* still matters implicitly: an extra
    column produces longer row tuples and a different fingerprint, which is how
    over-answering is caught.

    Element order **within** a row is significant, matching BIRD — ``(url, 2028)`` and
    ``(2028, url)`` answer different questions. Only row order is relaxed.

    The sort key is BIRD's ``cell_key``, not ``json.dumps`` of the row: the latter is
    deterministic, so it is adequate for self-comparison while producing a different byte
    string from the benchmark's for the same rows.
    """
    body = [[_coerce_cell(v) for v in row] for row in rows]
    if not order_sensitive:
        body = sorted(body, key=lambda row: tuple(_cell_key(c) for c in row))
    return body


def _coerce_cell(value: Any) -> Any:
    """One cell, BIRD's way: a number if it can be read as one, else a folded string.

    ``float(value)`` **first**, not ``isinstance(value, (int, float))`` with a
    ``str(value)`` fallback: ``Decimal`` is neither, so that spelling compared every
    Postgres ``numeric`` cell as a string and graded ``Decimal('100.00')`` against
    ``Decimal('100.0')`` as ``result_mismatch``. Every EX this repo produced before
    2026-08-06 is a retired underestimate of unknown size, and since the size scales with a
    schema's numeric-column density the cross-schema comparisons do not hold either.

    Two consequences of following BIRD exactly rather than "sensibly", both looser than one
    would design from scratch — but a grader stricter than its benchmark does not report the
    benchmark's number (``normalise_result_strict`` tags types; BIRD's EX does not use it):

    * ``float("1e5")`` succeeds, so the *string* ``"1e5"`` compares equal to ``100000.0``.
    * ``float(True)`` is ``1.0``, so ``True`` compares equal to ``1``.
    """
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
    """BIRD's ``cell_key``: a total order over ``None`` / float / str, in that order.

    Typed rather than lexicographic because ``sorted`` on mixed cells raises, and a sort that
    raises on one result set and not another is a grader that is a function of the data.
    """
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, float):
        return (1, value, "")
    return (2, 0.0, str(value))
