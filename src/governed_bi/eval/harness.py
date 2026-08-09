"""Eval harness — invoke serve ``compile_graph`` per question (ADR 0005 §4.1)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from governed_bi.eval.arms import ArmSpec, model_for_question
from governed_bi.eval.grade import grade_turn, result_fingerprint
from governed_bi.eval.oracle import oracle_grade
from governed_bi.eval.replay import PINNED_SCHEMAS_KEY
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

    ``session`` is how a **quotable** run is produced: every id and run constant is minted by
    ``Session.turn``. Without it turns come from :func:`_base_turn`, which fabricates
    ``corpus_content_hash`` — fine for a fixture, forged for a real corpus, because two runs
    over different corpora then compare equal.

    ``workers`` > 1 gives **each worker its own graph and connector**, and raises without a
    ``connector_factory``. ``psycopg`` connections are not thread-safe and this harness runs
    SQL twice per row (prediction and gold); sharing one interleaves result sets on a socket
    and surfaces as a *grading* mismatch, indistinguishable from a wrong answer.

    ``on_row(index, row)`` fires in completion order so a long run is crash-safe — a driver
    that writes only at the end is one interruption away from having measured nothing. The
    return value stays in input order.
    """
    order_sensitive_qids = order_sensitive_qids or frozenset()
    run_id = run_id or f"run-{uuid4().hex[:12]}"
    base_cfg = arm.build_configurable()

    if arm.oracle_only:
        connector = base_cfg.get("connector")
        if connector is None:
            raise ValueError("oracle arm requires connector in configurable")
        # Row by row with ``on_row``, like every other arm: a list comprehension here would
        # ignore ``on_row`` and let one unexecutable gold discard every row already computed.
        # ``oracle_grade`` records an execution failure as a crashed row.
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
    """One question, start to graded row.

    Shared by the serial and concurrent paths so there is one answer to what a measured turn
    is, rather than two implementations that must agree.
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
        # The session mints ids and run constants. The eval's own ``question_id`` stays in
        # the measurement row and deliberately cannot enter the turn: `Session.turn` digests
        # the question text for ``question_id`` so a re-serve is recognisable.
        turn = session.turn(
            str(question.get("question") or question.get("utterance") or qid),
            turn_index=1,
            thread_id=thread_id,
            identity={"token": f"eval-{run_id}"},
            # Passing evidence is one of the two conditions for EX being comparable to
            # published BIRD (the other is the grader, `eval/grade._coerce_cell`). An arm
            # that wants the harder no-hint condition omits the key from the question dict.
            evidence=question.get("evidence"),
        )
        # ``Session.turn`` writes the session's own knobs, so a driver that set a per-question
        # override (``tools/run_datalake_eval.py --top-n``) had it silently replaced and the
        # run served the register default under a header announcing the override. The
        # no-session path below has always honoured the question; these now agree.
        knobs = _turn_knobs(question, session)
        if knobs is not None:
            turn["knobs_resolved"] = knobs
    else:
        turn = _base_turn(question, run_id=run_id, arm=arm.name)
    # Inject route hits when no index so answered path can reach agent.
    if conf.get("index") is None and "facet_route_hits" not in turn:
        db = str(question.get("db_id") or "fixture")
        turn["facet_route_hits"] = [("facet_schema", db, 1.0)]

    # After `Session.turn`, which resets this channel with every other per-turn key. Only
    # present when `--replay-routing` attached one; a question the artifact did not cover is
    # left to route live and is counted as unpinned by `attach_pinned_routing`.
    if question.get(PINNED_SCHEMAS_KEY):
        turn[PINNED_SCHEMAS_KEY] = list(question[PINNED_SCHEMAS_KEY])

    result = compiled.invoke(turn, {"configurable": conf})
    row = project_turn(
        result,
        question=question,
        arm=arm.name,
        order_sensitive=qid in order_sensitive_qids,
        connector=conf.get("connector"),
    )
    row["run_id"] = run_id
    _evict(compiled, thread_id)
    return row


#: Abstentions that carry a statement, so the decline can be priced (see ``project_turn``)
#: without ever being counted. Narrower than "the engine abstained": a ``clarification`` is an
#: abstention too and has no statement to re-execute, so it is absent here and still reported
#: as one by the driver. Two questions, two sets -- merging them would either invent a
#: fingerprint for a turn that ran nothing or drop a decline from the abstention rate.
PRICED_ABSTENTIONS: frozenset[str] = frozenset({"capped", "refused"})


def _abstained_fingerprint(
    *,
    outcome: str,
    generated_sql: str | None,
    connector: Any | None,
    order_sensitive: bool,
    already_executed: bool,
) -> str | None:
    """Fingerprint of an abstained turn's last statement, or ``None``.

    ``None`` for every answered turn (``grade`` already has it), every turn with no statement,
    and every statement that will not run — the last of those is a real state, because a turn
    can be capped precisely because its statements kept failing.
    """
    if already_executed or outcome not in PRICED_ABSTENTIONS:
        return None
    if not generated_sql or connector is None:
        return None
    try:
        cols, rows, _ = connector.execute(str(generated_sql))
    except Exception:  # noqa: BLE001 — a statement that will not run has no fingerprint
        return None
    return result_fingerprint(list(cols), [list(r) for r in rows], order_sensitive=order_sensitive)


def _attempt_trace(execution: Any) -> list[dict[str, Any]]:
    """Per-attempt ``(layer, reason_code, passed)`` for the measurement row.

    ``CheckVerdict`` has carried ``failed_layer`` and ``reason_code`` all along and they
    stopped at the turn record, so a refused row in an artifact said *that* governance
    declined and never *which layer*. Reading the 2026-08-09 run therefore required replaying
    every refused statement through ``check()`` offline to learn that 18 of 21 were
    ``r_table_not_licensed`` — a retrieval failure the analysis had attributed to a
    guardrail false-positive. The field that would have said so already existed.
    """
    if not isinstance(execution, Mapping):
        return []
    trace: list[dict[str, Any]] = []
    for attempt in execution.get("attempts") or ():
        if not isinstance(attempt, Mapping):
            continue
        trace.append(
            {
                "layer": attempt.get("verdict_layer"),
                "reason_code": attempt.get("reason_code"),
                "passed": attempt.get("passed"),
                "path": attempt.get("path"),
            }
        )
    return trace


def _turn_knobs(question: Mapping[str, Any], session: Any) -> dict[str, Any] | None:
    """The configuration this question runs under: its own override, else the session's.

    ``None`` when neither carries one — absent, not ``{}``, because an empty mapping reads to
    ``measure.gates`` as "one configuration whose every knob is None".
    """
    for source in (question.get("knobs_resolved"), getattr(session, "knobs_resolved", None)):
        if isinstance(source, Mapping):
            return dict(source)
    return None


def _evict(compiled: Any, thread_id: str) -> None:
    """Drop this question's checkpoints. Never raises; an arm must not die over housekeeping.

    A worker holds one compiled graph for the whole arm and nothing else empties its saver —
    roughly 100 KB of checkpoint per question (``usage`` and ``answer`` accumulate and every
    superstep re-serialises them), retained until the process exits for state no later
    question reads.

    Called **after** the row is projected: a resumed clarification does read the checkpoint,
    so evicting earlier would trade a memory bound for a lost measurement. Per *thread*, not
    a blunt clear — with ``workers > 1`` several questions share the saver.
    """
    saver = getattr(getattr(compiled, "_app", compiled), "checkpointer", None)
    delete = getattr(saver, "delete_thread", None)
    if delete is None:
        return
    try:
        delete(thread_id)
    except Exception:  # noqa: BLE001 — a saver that cannot evict is a leak, not a failed turn
        return


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

    Thread-local rather than a pool of pre-built pairs, so a short run does not open
    ``workers`` connections it never uses.

    A question that raises is recorded as a **crashed row** rather than ending the arm, with
    the question and exception named — a systematic failure must be visible as a count, not
    as a shorter file.
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
                # The arm's configuration and the question's gold schema are known whether or
                # not the turn reached ``stamp``. Omitting them here would make one crash turn
                # the whole arm's knobs gate ``cannot_evaluate`` and drop the row out of the
                # funnel's routing stage, for a reason that has nothing to do with either.
                "knobs_resolved": _turn_knobs(question, session),
                "db_id": question.get("db_id"),
                "outcome": Outcome.crashed.value,
                "correct": False,
                "crashed": True,
                "quality_flags": list(question.get("quality_flags") or ()),
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
    # A paused turn is not a crashed one. `ask_user` interrupts and no node writes `answer`,
    # so defaulting to "crashed" reports a question asked of the analyst as an engine crash
    # with no stage and no exception class. (`python -m governed_bi.serve` exit code 4.)
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

    # **The abstention's price, measured but never scored.** A capped or refused turn keeps
    # ``correct=False`` — an engine that would not commit to a statement does not get credit
    # for it, and `grade_turn` owns that rule. But the rule has a cost, and until this ran
    # nobody knew what it was: of the 2026-08-09 full run's 133 capped turns, 23 had the
    # correct answer in their last statement. That is a scoring policy worth keeping and worth
    # pricing, and the two are only distinguishable if the number exists.
    #
    # A separate field, never folded into ``correct``: one merge of the two and the artifact
    # silently reports an engine that commits to everything.
    computed_fp = _abstained_fingerprint(
        outcome=outcome,
        generated_sql=generated_sql,
        connector=connector,
        order_sensitive=order_sensitive,
        already_executed=pred_columns is not None,
    )

    facet_channels = record.get("facet_channels")
    negative = state.get("negative") or record.get("negative") or {}
    negative_failed_open = (
        isinstance(negative, Mapping)
        and negative.get("outcome") == "error_failed_open"
    )
    guardrail_errors = int(record.get("guardrail_errors") or 0)
    n_re_served = int(state.get("n_re_served") or record.get("n_re_served") or 0)

    # Absent stays absent. ``{}`` would read to ``measure.gates`` as a real configuration in
    # which every knob resolved to None, so one arm of empties would *pass* the gate.
    knobs = record.get("knobs_resolved")
    if not isinstance(knobs, Mapping):
        knobs = state.get("knobs_resolved")

    return {
        "question_id": str(question["question_id"]),
        "arm": arm,
        # The gold schema, from the question. Every funnel stage under ``schema_routed`` is
        # conditional on it, and it was reachable only by re-reading the dataset file beside
        # the artifact — so a row could not be attributed to a routing failure on its own.
        "db_id": question.get("db_id"),
        # The configuration the turn ran under (register: Absence.never, "the corpus IS the
        # treatment" applies to knobs too). It reached ``stamp`` and stopped there: 1351/1351
        # rows of the 2026-08-07 run carry no such key, so the knobs gate reported
        # ``cannot_evaluate`` and no number could be joined to what produced it.
        "knobs_resolved": dict(knobs) if isinstance(knobs, Mapping) else None,
        "outcome": outcome,
        # Propagated, never coerced: ``bool(grade["correct"])`` here turns every
        # ``missing_gold`` into a wrong answer (see ``grade.grade_turn``).
        "correct": grade["correct"],
        "crashed": crashed,
        # What the *dataset* says is wrong with this question (leakage, a gold with no total
        # order, a degenerate gold). Carried on the row rather than filtered, so one artifact
        # can be read under more than one exclusion policy — see
        # :func:`~governed_bi.eval.datalake.attach_quality_flags`.
        "quality_flags": list(question.get("quality_flags") or ()),
        "generated_sql": generated_sql,
        "gold_sql": question.get("gold_sql"),
        "gold_fingerprint": grade.get("gold_fingerprint"),
        "pred_fingerprint": grade.get("pred_fingerprint"),
        "grade_detail": grade.get("detail"),
        "context_hash": context_hash,
        "facet_channels": facet_channels,
        "facet_degraded": bool(record.get("facet_degraded") or False),
        # Retrieval and crash attribution. Without `licensed`/`schemas` a row says EX=0 and
        # not *why*: a miss with the gold schema never licensed is a routing problem, a miss
        # with the right tables in hand is a generation problem — and an absent `reached_gold`
        # reads as zero, which once made a run contradict its own corpus (both figures
        # retired; citations.py). Without `error_type`, `outcome: "crashed"` carries no stage
        # and no exception class, which is unactionable on any run that crashes at all.
        "error_type": record.get("error_type") or state.get("failure", {}).get("error_type")
        if isinstance(state.get("failure"), Mapping)
        else record.get("error_type"),
        "licensed": list(record.get("licensed") or ()),
        "schemas": list(record.get("schemas") or ()),
        # Whether this row's shortlist was replayed rather than routed. An arm described as
        # pinned always has some fraction that was not (a question the artifact did not cover),
        # and per-row is the only place that fraction stays recoverable.
        "routing_pinned": bool(question.get(PINNED_SCHEMAS_KEY)),
        # Which layer refused, per attempt. See `_attempt_trace`.
        "attempts": _attempt_trace(record.get("execution")),
        # Set only on abstained turns; never folded into `correct`. See `_abstained_fingerprint`.
        "computed_fingerprint": computed_fp,
        "computed_correct": (
            None if computed_fp is None or not gold_fp else computed_fp == str(gold_fp)
        ),
        "terminal_reason": record.get("terminal_reason"),
        # Carried so the run can be counted: `observed_tokens` reads it, and without it a
        # batch reports no calls at all, reading as a free run rather than an unmeasured one.
        # Tokens only — `measure/price.py` is deleted, so cost is the provider's number.
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
