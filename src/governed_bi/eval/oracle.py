"""Oracle-only ceiling: execute gold SQL, no model (ADR 0005 step 15)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from governed_bi.eval.grade import GradeResult, grade_results, result_fingerprint

__all__ = ["oracle_grade", "OracleQuestion"]


#: One fixture / dataset question for the free grader ceiling.
OracleQuestion = Mapping[str, Any]


def oracle_grade(
    question: OracleQuestion,
    connector: Any,
    *,
    order_sensitive_qids: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Run ``gold_sql`` on ``connector`` and grade the result against itself / hash.

    Returns a turn-shaped dict with ``correct=True`` when gold is self-consistent.
    No serve graph, no model — the free rescale of every paid ladder.
    """
    qid = str(question["question_id"])
    sql = str(question["gold_sql"])
    order_sensitive = qid in (order_sensitive_qids or frozenset())

    columns, rows, _truncated = connector.execute(sql)
    pred = (list(columns), [list(r) for r in rows])

    gold_fp = question.get("gold_fingerprint")
    if gold_fp:
        pred_fp = result_fingerprint(pred[0], pred[1], order_sensitive=order_sensitive)
        grade = GradeResult(
            correct=str(gold_fp) == pred_fp,
            gold_fingerprint=str(gold_fp),
            pred_fingerprint=pred_fp,
            detail="match" if str(gold_fp) == pred_fp else "result_mismatch",
        )
    elif question.get("gold_columns") is not None and question.get("gold_rows") is not None:
        grade = grade_results(
            pred_columns=pred[0],
            pred_rows=pred[1],
            gold_columns=list(question["gold_columns"]),  # type: ignore[arg-type]
            gold_rows=list(question["gold_rows"]),  # type: ignore[arg-type]
            order_sensitive=order_sensitive,
        )
    else:
        # Self-grade: executing gold against itself must match (ceiling sanity).
        grade = grade_results(
            pred_columns=pred[0],
            pred_rows=pred[1],
            gold_columns=pred[0],
            gold_rows=pred[1],
            order_sensitive=order_sensitive,
        )

    return {
        "question_id": qid,
        "arm": "oracle",
        "outcome": "answered",
        "correct": bool(grade["correct"]),
        "crashed": False,
        "generated_sql": sql,
        "gold_sql": sql,
        "gold_fingerprint": grade.get("gold_fingerprint"),
        "pred_fingerprint": grade.get("pred_fingerprint"),
        "grade_detail": grade.get("detail"),
        "context_hash": None,
        "facet_channels": None,
        "facet_degraded": False,
        "guardrail_error": False,
        "re_served": False,
        "negative_failed_open": False,
    }
