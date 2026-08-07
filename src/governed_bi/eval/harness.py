"""Eval harness — invoke serve ``compile_graph`` per question (ADR 0005 §4.1)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from governed_bi.eval.arms import ArmSpec, model_for_question
from governed_bi.eval.grade import grade_turn
from governed_bi.eval.oracle import oracle_grade
from governed_bi.register.stages import Outcome
from governed_bi.serve.graph import compile_graph

__all__ = ["run_arm", "run_comparison", "project_turn"]


def run_arm(
    questions: Sequence[Mapping[str, Any]],
    arm: ArmSpec,
    *,
    order_sensitive_qids: frozenset[str] | None = None,
    graph: Any | None = None,
    run_id: str | None = None,
    session: Any | None = None,
    workers: int = 1,
    connector_factory: Any | None = None,
    on_row: Any | None = None,
) -> list[dict[str, Any]]:
    """Run every question under one arm; return graded turn rows, **in input order**.

    ``session`` is how a **quotable** run is produced. Without it each turn comes from
    :func:`_base_turn`, which fabricates ``corpus_content_hash`` as ``f"corpus-{arm}"`` —
    fine for a fixture, forged for a real corpus, because two runs over two different
    corpora then compare equal. With a session, every id and every run constant is minted
    by ``Session.turn``, which is the one place allowed to mint them.

    ``workers`` > 1 runs questions concurrently, and **each worker gets its own graph and
    its own connector**. That is not tidiness: ``psycopg`` connections are not thread-safe,
    and this harness executes SQL on the connector twice per row — once for the prediction
    and once for the gold — on top of whatever the turn itself ran. Sharing one connection
    across threads interleaves two result sets on one socket, and the failure surfaces as a
    *grading* mismatch, which is indistinguishable from a wrong answer. So ``workers`` > 1
    without a ``connector_factory`` raises rather than racing.

    ``on_row(index, row)`` is called as each row completes, from the calling thread's
    perspective in completion order. It exists so a long run is crash-safe: a 1 351-question
    arm at four workers is hours, and a driver that writes only at the end is one
    interruption away from having measured nothing. The return value is still ordered by
    input, so a caller using both gets streaming *and* determinism.
    """
    order_sensitive_qids = order_sensitive_qids or frozenset()
    run_id = run_id or f"run-{uuid4().hex[:12]}"
    base_cfg = arm.build_configurable()

    if arm.oracle_only:
        connector = base_cfg.get("connector")
        if connector is None:
            raise ValueError("oracle arm requires connector in configurable")
        # **Row by row, with ``on_row``, like every other arm.** This was one list
        # comprehension sitting before the concurrency dispatch, so it ignored ``on_row``
        # entirely and one gold statement that failed to execute ended the arm and discarded
        # every row already computed. ``oracle_grade`` now records an execution failure as a
        # crashed row, and the loop is here so a long oracle pass is crash-safe for the same
        # reason the paid arms are.
        rows: list[dict[str, Any]] = []
        for index, question in enumerate(questions):
            row = oracle_grade(question, connector, order_sensitive_qids=order_sensitive_qids)
            row["arm"] = arm.name
            row["run_id"] = run_id
            rows.append(row)
            if on_row is not None:
                on_row(index, row)
        return rows

    workers = max(1, int(workers))
    if workers > 1 and connector_factory is None:
        raise ValueError(
            f"workers={workers} needs a connector_factory: psycopg connections are not "
            "thread-safe, and this harness runs the prediction and the gold statement on "
            "the connector for every row. Sharing one across threads interleaves result "
            "sets on one socket and the damage appears as a grading mismatch, which reads "
            "as a wrong answer rather than as a broken harness."
        )

    if workers == 1:
        compiled = graph if graph is not None else compile_graph()
        out: list[dict[str, Any]] = []
        for index, question in enumerate(questions):
            row = _run_one(
                question,
                arm=arm,
                base_cfg=base_cfg,
                compiled=compiled,
                session=session,
                run_id=run_id,
                order_sensitive_qids=order_sensitive_qids,
            )
            out.append(row)
            if on_row is not None:
                on_row(index, row)
        return out

    return _run_concurrently(
        questions,
        arm=arm,
        base_cfg=base_cfg,
        session=session,
        run_id=run_id,
        order_sensitive_qids=order_sensitive_qids,
        workers=workers,
        connector_factory=connector_factory,
        on_row=on_row,
    )


def _run_one(
    question: Mapping[str, Any],
    *,
    arm: ArmSpec,
    base_cfg: Mapping[str, Any],
    compiled: Any,
    session: Any,
    run_id: str,
    order_sensitive_qids: frozenset[str],
    connector: Any | None = None,
) -> dict[str, Any]:
    """One question, start to graded row. The body ``run_arm`` used to inline.

    Extracted so the serial and concurrent paths are the **same** code rather than two
    implementations that must agree — a second copy here would be a second answer to what
    a measured turn is, which is the shape this file's own ``_base_turn`` note is about.
    """
    qid = str(question["question_id"])
    conf = dict(base_cfg)
    if connector is not None:
        conf["connector"] = connector
    model = model_for_question(conf, qid)
    if model is not None:
        conf["agent_model"] = model
    conf.pop("scripted_sql_lookup", None)
    thread_id = f"{run_id}-{arm.name}-{qid}"
    conf["thread_id"] = thread_id

    if session is not None:
        # The session mints the ids and the run constants; the eval's own ``question_id``
        # stays in the measurement row, where `project_turn` reads it. It deliberately
        # cannot enter the turn: `Session.turn` digests the question text for
        # ``question_id`` so a re-serve is recognisable, and letting a caller set it would
        # break that.
        turn = session.turn(
            str(question.get("question") or question.get("utterance") or qid),
            turn_index=1,
            thread_id=thread_id,
            identity={"token": f"eval-{run_id}"},
            # Loaded by `eval/datalake.py:load_questions` since the beginning and consumed by
            # nothing until now, which made every EX a no-evidence number. Passing it is one
            # of the two conditions for the figure being comparable to published BIRD; the
            # other is the grader, and for the whole of v2 that half was false — see
            # `eval/grade._coerce_cell`. An arm that wants the harder no-hint condition omits
            # the key from the question dict.
            evidence=question.get("evidence"),
        )
    else:
        turn = _base_turn(question, run_id=run_id, arm=arm.name)
    # Inject route hits when no index so answered path can reach agent.
    if conf.get("index") is None and "facet_route_hits" not in turn:
        db = str(question.get("db_id") or "fixture")
        turn["facet_route_hits"] = [("facet_schema", db, 1.0)]

    result = compiled.invoke(turn, {"configurable": conf})
    row = project_turn(
        result,
        question=question,
        arm=arm.name,
        order_sensitive=qid in order_sensitive_qids,
        connector=conf.get("connector"),
    )
    row["run_id"] = run_id
    return row


def _run_concurrently(
    questions: Sequence[Mapping[str, Any]],
    *,
    arm: ArmSpec,
    base_cfg: Mapping[str, Any],
    session: Any,
    run_id: str,
    order_sensitive_qids: frozenset[str],
    workers: int,
    connector_factory: Any,
    on_row: Any | None,
) -> list[dict[str, Any]]:
    """``workers`` threads, one graph and one connector each, results in input order.

    Thread-local rather than a pool of pre-built pairs, because the count of *live* workers
    is what decides how many connections exist — building ``workers`` connectors up front
    and handing them out would open all of them even on a short run.

    A question that raises is recorded as a **crashed row** rather than ending the arm. One
    provider timeout at question 900 of 1 351 must not discard 899 measured turns; the row
    says which question and which exception, so a systematic failure is still visible as a
    count rather than as a shorter file.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    local = threading.local()

    def worker_state() -> tuple[Any, Any]:
        if not hasattr(local, "pair"):
            local.pair = (compile_graph(), connector_factory())
        return local.pair

    def run_index(index: int) -> tuple[int, dict[str, Any]]:
        compiled, connector = worker_state()
        question = questions[index]
        try:
            row = _run_one(
                question,
                arm=arm,
                base_cfg=base_cfg,
                compiled=compiled,
                session=session,
                run_id=run_id,
                order_sensitive_qids=order_sensitive_qids,
                connector=connector,
            )
        except Exception as err:  # noqa: BLE001 — one bad question must not end the arm
            row = {
                "question_id": str(question.get("question_id")),
                "arm": arm.name,
                "run_id": run_id,
                "outcome": Outcome.crashed.value,
                "correct": False,
                "crashed": True,
                "error_type": type(err).__name__,
                "grade_detail": f"harness: {type(err).__name__}: {err}",
                "licensed": [],
                "schemas": [],
                "usage": [],
            }
        return index, row

    results: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index, row in pool.map(run_index, range(len(questions))):
            results[index] = row
            if on_row is not None:
                on_row(index, row)
    return [results[i] for i in range(len(questions))]


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
    # **A paused turn is not a crashed one.** `ask_user` interrupts, no node writes
    # `answer`, and this defaulted to "crashed" — so 7 of 57 questions in a live batch were
    # reported as engine crashes with no stage and no exception class, while every one of
    # them had simply asked the analyst a question. `python -m governed_bi.serve` has exit
    # code 4 for exactly this distinction; the harness had none.
    interrupted = bool(state.get("__interrupt__")) and not answer
    if interrupted:
        outcome = Outcome.clarification.value
    else:
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
        # **Retrieval attribution, and the reason it is here.** A row could say EX=0 and
        # not say *why*: a miss because the gold schema was never licensed is a routing
        # problem, and a miss with the right tables in hand is a generation problem. Without
        # these two, a live run over 57 questions reported `reached_gold 0/57` on a corpus
        # measured at 0.608 — the number was absent, not zero, and absent read as zero.
        # Crash attribution. `outcome: "crashed"` with no stage and no exception class is
        # unactionable, and a run where 16% of turns crash needs to say where.
        "error_type": record.get("error_type") or state.get("failure", {}).get("error_type")
        if isinstance(state.get("failure"), Mapping)
        else record.get("error_type"),
        "licensed": list(record.get("licensed") or ()),
        "schemas": list(record.get("schemas") or ()),
        "terminal_reason": record.get("terminal_reason"),
        # Carried so the run can be counted. `observed_tokens` reads it; without it a batch
        # reports no calls at all, which reads as a free run rather than an unmeasured one.
        # It used to say **priced** -- `measure/price.py` is deleted, and tokens are as far
        # as this repository goes now: what they cost is the provider's number.
        # which is honest and useless.
        "usage": list(record.get("usage") or ()),
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
        "evidence": str(question.get("evidence") or ""),
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
