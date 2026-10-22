"""Execute gold SQL and compare it to an independent record (ADR 0005).

The record is a ``gold_fingerprint``, or ``gold_columns`` plus ``gold_rows``.
Without it, executed gold compared with itself is true for any statement, so
the row is unmeasured rather than scored.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from governed_bi.eval.grade import GradeResult, grade_results, result_fingerprint
from governed_bi.registry.stages import Outcome

__all__ = ["oracle_grade", "OracleQuestion", "NO_INDEPENDENT_GOLD"]


OracleQuestion = Mapping[str, Any]

NO_INDEPENDENT_GOLD = (
    "no_independent_gold: the question carries neither gold_fingerprint nor "
    "gold_columns+gold_rows, so the only available comparison is the executed gold against "
    "itself, which is true by construction"
)


def oracle_grade(
    question: OracleQuestion,
    connector: Any,
    *,
    order_sensitive_qids: frozenset[str] | None = None,
) -> dict[str, Any]:
    """``correct`` is ``None`` when the independent gold is missing. A gold that fails to execute is ``crashed``."""
    qid = str(question["question_id"])
    sql = str(question["gold_sql"])
    order_sensitive = qid in (order_sensitive_qids or frozenset())

    try:
        columns, rows, _truncated = connector.execute(sql)
    except Exception as err:  # noqa: BLE001 — the row is the point
        # Caught so one unexecutable gold does not end the config and drop rows
        # already written. The harness records the crashed row and continues.
        return _row(
            qid,
            sql,
            outcome=Outcome.crashed.value,
            correct=None,
            crashed=True,
            grade_detail=f"gold_exec_failed: {type(err).__name__}: {err}",
            error_type=type(err).__name__,
        )

    pred_columns = list(columns)
    pred_rows = [list(r) for r in rows]

    gold_fp = question.get("gold_fingerprint")
    if gold_fp:
        pred_fp = result_fingerprint(pred_columns, pred_rows, order_sensitive=order_sensitive)
        matched = str(gold_fp) == pred_fp
        grade = GradeResult(
            correct=matched,
            gold_fingerprint=str(gold_fp),
            pred_fingerprint=pred_fp,
            detail="match" if matched else "result_mismatch",
        )
    elif question.get("gold_columns") is not None and question.get("gold_rows") is not None:
        grade = grade_results(
            pred_columns=pred_columns,
            pred_rows=pred_rows,
            gold_columns=list(question["gold_columns"]),  # type: ignore[arg-type]
            gold_rows=list(question["gold_rows"]),  # type: ignore[arg-type]
            order_sensitive=order_sensitive,
        )
    else:
        return _row(
            qid,
            sql,
            outcome=Outcome.answered.value,
            correct=None,
            crashed=False,
            grade_detail=NO_INDEPENDENT_GOLD,
            pred_fingerprint=result_fingerprint(
                pred_columns, pred_rows, order_sensitive=order_sensitive
            ),
        )

    return _row(
        qid,
        sql,
        outcome=Outcome.answered.value,
        # Propagated, never coerced: an ungradeable row is ``None``, not ``False``.
        correct=grade["correct"],
        crashed=False,
        grade_detail=grade.get("detail"),
        gold_fingerprint=grade.get("gold_fingerprint"),
        pred_fingerprint=grade.get("pred_fingerprint"),
    )


def _row(
    qid: str,
    sql: str,
    *,
    outcome: str,
    correct: bool | None,
    crashed: bool,
    grade_detail: str | None,
    gold_fingerprint: str | None = None,
    pred_fingerprint: str | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    """``pred_fingerprint`` stays set when unmeasured, so a later run can store it as ``gold_fingerprint``."""
    return {
        "question_id": qid,
        "config": "oracle",
        "outcome": outcome,
        "correct": correct,
        "crashed": crashed,
        "generated_sql": sql,
        "gold_sql": sql,
        "gold_fingerprint": gold_fingerprint,
        "pred_fingerprint": pred_fingerprint,
        "grade_detail": grade_detail,
        "error_type": error_type,
        "context_hash": None,
        "channel_scoring": None,
        "channel_degraded": False,
        "guardrail_error": False,
        "re_served": False,
        "negative_failed_open": False,
    }
