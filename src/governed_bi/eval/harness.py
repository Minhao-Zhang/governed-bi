"""Eval harness — invoke serve ``compile_graph`` per question (ADR 0005 §4.1)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from governed_bi.eval.arms import ArmSpec, model_for_question
from governed_bi.eval.grade import grade_turn
from governed_bi.eval.oracle import oracle_grade
from governed_bi.serve.graph import compile_graph

__all__ = ["run_arm", "run_comparison", "project_turn"]


def run_arm(
    questions: Sequence[Mapping[str, Any]],
    arm: ArmSpec,
    *,
    order_sensitive_qids: frozenset[str] | None = None,
    graph: Any | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Run every question under one arm; return graded turn rows."""
    order_sensitive_qids = order_sensitive_qids or frozenset()
    run_id = run_id or f"run-{uuid4().hex[:12]}"
    base_cfg = arm.build_configurable()

    if arm.oracle_only:
        connector = base_cfg.get("connector")
        if connector is None:
            raise ValueError("oracle arm requires connector in configurable")
        rows = [
            oracle_grade(q, connector, order_sensitive_qids=order_sensitive_qids)
            for q in questions
        ]
        for row in rows:
            row["arm"] = arm.name
            row["run_id"] = run_id
        return rows

    compiled = graph if graph is not None else compile_graph()
    out: list[dict[str, Any]] = []
    for q in questions:
        qid = str(q["question_id"])
        conf = dict(base_cfg)
        model = model_for_question(conf, qid)
        if model is not None:
            conf["agent_model"] = model
        conf.pop("scripted_sql_lookup", None)
        thread_id = f"{run_id}-{arm.name}-{qid}"
        conf["thread_id"] = thread_id

        turn = _base_turn(q, run_id=run_id, arm=arm.name)
        # Inject route hits when no index so answered path can reach agent.
        if conf.get("index") is None and "facet_route_hits" not in turn:
            db = str(q.get("db_id") or "fixture")
            turn["facet_route_hits"] = [("facet_schema", db, 1.0)]

        result = compiled.invoke(turn, {"configurable": conf})
        row = project_turn(
            result,
            question=q,
            arm=arm.name,
            order_sensitive=qid in order_sensitive_qids,
            connector=conf.get("connector"),
        )
        row["run_id"] = run_id
        out.append(row)
    return out


def run_comparison(
    questions: Sequence[Mapping[str, Any]],
    arms: Sequence[ArmSpec],
    *,
    order_sensitive_qids: frozenset[str] | None = None,
    run_id: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Run each arm; return ``{arm_name: turn_rows}``."""
    run_id = run_id or f"run-{uuid4().hex[:12]}"
    return {
        arm.name: run_arm(
            questions,
            arm,
            order_sensitive_qids=order_sensitive_qids,
            run_id=run_id,
        )
        for arm in arms
    }


def project_turn(
    state: Mapping[str, Any],
    *,
    question: Mapping[str, Any],
    arm: str,
    order_sensitive: bool = False,
    connector: Any = None,
) -> dict[str, Any]:
    """Project a serve final state into a measurement turn row."""
    answer = state.get("answer") or {}
    record = answer.get("record") if isinstance(answer, Mapping) else None
    if not isinstance(record, Mapping):
        record = {}
    outcome = str(answer.get("outcome") or record.get("outcome") or "crashed")
    crashed = outcome == "crashed"

    delivery = state.get("delivery") or {}
    context_hash = None
    if isinstance(delivery, Mapping):
        context_hash = delivery.get("context_hash")
    if context_hash is None:
        context_hash = record.get("context_hash")

    generated_sql = state.get("generated_sql") or record.get("generated_sql")
    pred_columns = None
    pred_rows = None
    if (
        outcome == "answered"
        and generated_sql
        and connector is not None
        and question.get("gold_sql")
    ):
        try:
            cols, rows, _ = connector.execute(str(generated_sql))
            pred_columns = list(cols)
            pred_rows = [list(r) for r in rows]
        except Exception:  # noqa: BLE001 — grade as missing prediction
            pred_columns, pred_rows = None, None

    gold_fp = question.get("gold_fingerprint")
    gold_columns = question.get("gold_columns")
    gold_rows = question.get("gold_rows")
    if gold_fp is None and connector is not None and question.get("gold_sql"):
        try:
            gcols, grows, _ = connector.execute(str(question["gold_sql"]))
            gold_columns = list(gcols)
            gold_rows = [list(r) for r in grows]
        except Exception:  # noqa: BLE001
            pass

    grade = grade_turn(
        outcome=outcome,
        pred_columns=pred_columns,
        pred_rows=pred_rows,
        gold_columns=list(gold_columns) if gold_columns is not None else None,
        gold_rows=list(gold_rows) if gold_rows is not None else None,
        gold_fingerprint=str(gold_fp) if gold_fp else None,
        order_sensitive=order_sensitive,
    )

    facet_channels = record.get("facet_channels")
    negative = state.get("negative") or record.get("negative") or {}
    negative_failed_open = (
        isinstance(negative, Mapping)
        and negative.get("outcome") == "error_failed_open"
    )
    guardrail_errors = int(record.get("guardrail_errors") or 0)
    n_re_served = int(state.get("n_re_served") or record.get("n_re_served") or 0)

    return {
        "question_id": str(question["question_id"]),
        "arm": arm,
        "outcome": outcome,
        "correct": bool(grade["correct"]),
        "crashed": crashed,
        "generated_sql": generated_sql,
        "gold_sql": question.get("gold_sql"),
        "gold_fingerprint": grade.get("gold_fingerprint"),
        "pred_fingerprint": grade.get("pred_fingerprint"),
        "grade_detail": grade.get("detail"),
        "context_hash": context_hash,
        "facet_channels": facet_channels,
        "facet_degraded": bool(record.get("facet_degraded") or False),
        "guardrail_error": guardrail_errors > 0,
        "re_served": n_re_served > 0,
        "negative_failed_open": bool(negative_failed_open),
        "refused_by": answer.get("refused_by") if isinstance(answer, Mapping) else None,
        "failed_stage": answer.get("failed_stage") if isinstance(answer, Mapping) else None,
    }


def _base_turn(question: Mapping[str, Any], *, run_id: str, arm: str) -> dict[str, Any]:
    qid = str(question["question_id"])
    db = str(question.get("db_id") or "fixture")
    return {
        "question": str(question.get("question") or question.get("utterance") or qid),
        "thread_id": f"{run_id}-{arm}-{qid}",
        "turn_index": 1,
        "run_id": run_id,
        "turn_id": f"turn-{arm}-{qid}",
        "question_id": qid,
        "db_id": db,
        "attempt_id": f"attempt-{arm}-{qid}",
        "corpus_content_hash": str(question.get("corpus_content_hash") or f"corpus-{arm}"),
        "prompt_set_hash": str(question.get("prompt_set_hash") or "prompt-eval"),
        "knobs_resolved": dict(question.get("knobs_resolved") or {"route_top_n": 3}),
        "n_re_served": 0,
        "messages": [],
        "usage": [],
        "clarifications": [],
        "identity": question.get("identity") or {"token": f"eval-{run_id}"},
    }
