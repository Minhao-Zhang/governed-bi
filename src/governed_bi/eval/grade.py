"""Per-row EX grading against gold **results** (ADR 0005 §4.1).

Compares executed result sets, not SQL strings. Order-insensitive by default;
order-sensitive question ids keep row order.

**The comparison is BIRD-Obfuscation's ``normalise_result``, transcribed.** Not
"aligned with", not "equivalent to" — the same function, so that a fingerprint
produced here and one produced by ``pipeline/_db.py``'s
``hash_normalised_result`` are the same 64 hex characters for the same rows.
:func:`result_fingerprint` is that hash. Why it had to be transcribed rather
than approximated is in :func:`_coerce_cell`.
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

    Byte-identical to ``hash_normalised_result`` in BIRD-Obfuscation's
    ``pipeline/_db.py`` when ``order_sensitive`` is false: the same normaliser, the same
    ``json.dumps(..., separators=(",", ":"), ensure_ascii=False)``, the same digest of the
    same list-of-lists. That equality is the point of the field, not a coincidence — it is
    what lets a ``gold_fingerprint`` computed by the benchmark's own pipeline be dropped
    into a question row here and compared, instead of re-executing every gold statement.

    ``columns`` is accepted and **not hashed**; see :func:`_normalise`.

    ``order_sensitive=True`` skips the sort and is therefore *stricter* than BIRD, whose
    comparators always sort. That is deliberate — the dataset ships
    ``order_sensitive_qids.json`` for golds where row order carries the answer — but it
    means those fingerprints are not BIRD-comparable, and a gold fingerprint from the
    benchmark pipeline must not be used for those question ids.
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

    Crashes and refusals are **incorrect**, never collapsed into each other —
    ``correct=False`` with ``detail`` naming the outcome. Only ``answered`` turns
    with a comparable result can be ``correct=True``.

    **The three values are not two.** A turn the system got wrong and a turn this grader could
    not judge are different facts, and only one of them is about the system. So a missing *gold*
    is ``None`` (nothing to compare against — ours), while a missing *prediction* stays ``False``
    (the model produced SQL that would not execute — theirs). Callers must propagate the ``None``
    rather than coerce it: ``bool(None)`` is ``False``, which is precisely the collapse
    :meth:`~governed_bi.measure.population.Population.count` refuses to make downstream.
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
            # **``None``, not ``False``: no gold is the instrument failing, not the model.**
            #
            # It returned ``False`` here, and that is the defect ``Population.count`` carries an
            # import-time guard against — "an absent outcome is not a negative one" — arriving one
            # layer upstream of the guard, where nothing was watching. A gold that will not
            # execute (a connection blip mid-run, a schema that moved) was recorded as a question
            # the system got wrong, indistinguishable in the artifact from a real mistake, and it
            # deflated EX by however many of them there were.
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

    **Column names are deliberately not part of the fingerprint.** They were, and it made
    this grader stricter than the benchmark it implements. BIRD's EX compares result
    *values* — a prediction is correct when its rows match the gold's, whatever the
    projection happens to be called. Including the names meant
    ``SELECT COUNT(*) AS paper_count`` graded **wrong** against a gold of ``SELECT COUNT(*)``
    with both returning ``100``, and the penalty correlated with how verbose the model was
    about aliasing rather than with whether it was right. Measured on the xhigh arm: 5% of
    answerable-but-wrong turns were exactly this, identical values under a different name.

    ``columns`` stays in the signature because the caller has it and the *count* still
    matters implicitly — a prediction with an extra column produces longer row tuples and so
    a different fingerprint, which is correct and is how over-answering is caught.

    Element order **within** a row is significant, matching BIRD: ``(url, 2028)`` and
    ``(2028, url)`` are different answers to different questions. Only row order is relaxed.

    The sort key is BIRD's ``cell_key``, not ``json.dumps`` of the row. Sorting the rendered
    JSON was deterministic and therefore adequate for self-comparison, which is exactly why
    it survived: it produced a *different byte string* from the benchmark's for the same
    rows, and nothing here ever compared the two.
    """
    body = [[_coerce_cell(v) for v in row] for row in rows]
    if not order_sensitive:
        body = sorted(body, key=lambda row: tuple(_cell_key(c) for c in row))
    return body


def _coerce_cell(value: Any) -> Any:
    """One cell, BIRD's way: a number if it can be read as one, else a folded string.

    ``float(value)`` **first**, and this is the whole finding. The predecessor was
    ``isinstance(value, (int, float))`` with ``return str(value)`` as the fallback, and
    ``Decimal`` is neither ``int`` nor ``float`` — so every Postgres ``numeric`` cell was
    compared as a string. ``Decimal('100.00')`` and ``Decimal('100.0')`` are the same
    number and were different strings; so were ``Decimal('0.5')`` and ``0.5``, and ``1.0``
    and ``1``. All of them graded ``correct=False`` with ``detail="result_mismatch"``,
    indistinguishable in the artifact from a genuinely wrong answer.

    So every EX number produced before this was an underestimate, and — because the size of
    the underestimate is a function of how many numeric columns the schema has — the
    cross-schema comparisons did not hold either.

    Two consequences of following BIRD exactly rather than "sensibly":

    * ``float("1e5")`` succeeds, so the *string* ``"1e5"`` compares equal to ``100000.0``.
    * ``float(True)`` is ``1.0``, so ``True`` compares equal to ``1``.

    Both are looser than one would design from scratch. They are what the benchmark being
    graded does, and a grader that is stricter than its benchmark reports a number that is
    not the benchmark's number. ``normalise_result_strict`` is the variant that tags types;
    it is not what BIRD's EX uses.
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
