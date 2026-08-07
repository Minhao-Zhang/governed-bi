"""Oracle-only ceiling: execute gold SQL, no model (ADR 0005 step 15).

**What this arm can and cannot establish.** It re-executes each question's ``gold_sql`` on
this connector and compares the result against an *independent* record of what that statement
returns — a ``gold_fingerprint``, or ``gold_columns`` + ``gold_rows``. When it drops below
1.000 the finding is real and it is not about the model: the grader disagrees with the
reference, or this engine returns something different from the one the reference was taken on,
or the harness lost rows on the way. That is the "grader ceiling".

It requires that independent record. **Without one there is no measurement here at all**, and
that is the correction this module carries: for most of v2 the fallback branch called
``grade_results(pred_columns=pred[0], pred_rows=pred[1], gold_columns=pred[0],
gold_rows=pred[1])`` — the executed gold fingerprinted **against itself** — which returns
``correct=True`` for any statement whatsoever. ``oracle_grade({"question_id": "q",
"gold_sql": "SELECT 'garbage' AS wrong"}, conn)`` returned ``correct=True``. No producer in
the repository supplies the three keys that would have taken a different branch, so this was
the branch every run took, and ``tests/eval/test_eval_contract.py`` enshrined ``ex.value ==
1.000`` as the contract.

The arm exists to show that the grader is not the bottleneck. It could not show anything, and
it was cited as having shown it — while the grader *was* a bottleneck (§5.1: every Postgres
``numeric`` cell compared as a string).

A question with no independent gold now yields ``correct=None``, which
``measure/population.Population.count`` reads as *unmeasured* rather than as a zero, so the
arm's headline EX is ``unmeasured`` with a reason attached. Neither 1.000 nor 0.000: both of
those are claims, and there is nothing here to claim.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from governed_bi.eval.grade import GradeResult, grade_results, result_fingerprint
from governed_bi.register.stages import Outcome

__all__ = ["oracle_grade", "OracleQuestion", "NO_INDEPENDENT_GOLD"]


#: One fixture / dataset question for the free grader ceiling.
OracleQuestion = Mapping[str, Any]

#: ``grade_detail`` for a question this arm cannot grade.
#:
#: Spelled out rather than left as a bare string because it is what a reader of the artifact
#: sees instead of a score, and it has to say why the cell is empty.
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
    """Execute ``gold_sql`` and compare it against the question's independent gold.

    Returns a turn-shaped row. ``correct`` is ``None`` — unmeasured — when the question
    supplies no independent gold, and when the gold statement itself fails to execute the row
    is ``crashed`` rather than incorrect: a gold that does not run is a defect in the dataset
    or the engine, not a wrong answer.

    No serve graph, no model.
    """
    qid = str(question["question_id"])
    sql = str(question["gold_sql"])
    order_sensitive = qid in (order_sensitive_qids or frozenset())

    try:
        columns, rows, _truncated = connector.execute(sql)
    except Exception as err:  # noqa: BLE001 — the row is the point
        # **Caught here rather than left to propagate.** The harness ran this arm as a bare
        # list comprehension, so one gold statement that failed to execute ended the arm and
        # discarded every row already computed — and on a 1 351-question dataset that is
        # hours of work thrown away by one bad statement, reported as a shorter file.
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
        correct=bool(grade["correct"]),
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
    """One oracle row. Built in one place so every branch produces the same keys.

    ``pred_fingerprint`` is filled even on the unmeasured branch: the arm still *executed* the
    gold statement, and the digest of what it returned is the thing a later run needs in order
    to become measurable. It is the field to harvest into ``gold_fingerprint``.
    """
    return {
        "question_id": qid,
        "arm": "oracle",
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
        "facet_channels": None,
        "facet_degraded": False,
        "guardrail_error": False,
        "re_served": False,
        "negative_failed_open": False,
    }
