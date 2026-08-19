"""Eval harness — invoke serve ``compile_graph`` per question (ADR 0005 §4.1)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from governed_bi.eval.arms import ArmSpec, model_for_question
from governed_bi.eval.oracle import oracle_grade
from governed_bi.eval.projection import project_turn
from governed_bi.eval.replay import PINNED_SCHEMAS_KEY
from governed_bi.register.stages import Outcome
from governed_bi.serve.graph import compile_durable

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
        # A caller-supplied graph is the caller's to close; one we make here is ours. Durable so
        # that `HARNESS_DB` -- not the conversation store -- carries the arm's threads.
        compiled = graph if graph is not None else compile_durable()
        ours = graph is None
        out: list[dict[str, Any]] = []
        try:
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
        finally:
            # `getattr`: a caller-supplied graph and the test doubles that stand in for one are
            # not required to be closeable, and housekeeping must not turn into the arm's error.
            if ours:
                close = getattr(compiled, "close", None)
                if close is not None:
                    close()
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
    if saver is None:
        return
    # A durable saver is async-only: `AsyncSqliteSaver.delete_thread` *exists* and raises
    # `NotImplementedError`, so probing for the attribute and swallowing the failure would
    # silently stop evicting and grow the harness database without bound. Prefer the async
    # method, driven on the graph's own pinned loop -- which is the only loop its connection
    # will answer on.
    run_coro = getattr(compiled, "run_coro", None)
    adelete = getattr(saver, "adelete_thread", None)
    if run_coro is not None and adelete is not None and getattr(compiled, "_loop", None) is not None:
        try:
            run_coro(adelete(thread_id))
        except Exception:  # noqa: BLE001 — a saver that cannot evict is a leak, not a failed turn
            return
        return
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
    from concurrent.futures import ThreadPoolExecutor, as_completed

    local = threading.local()
    # Every graph this pool builds, so `finally` can close them all. A durable saver binds its
    # `aiosqlite` connection to the loop that opened it, so each worker gets its **own** graph,
    # loop and connection -- sharing one across threads would drive it from a loop it will not
    # answer on. They contend on one SQLite file as writers; that is the cost of durability here,
    # and `workers` defaults to 1.
    built: list[Any] = []
    built_lock = threading.Lock()

    def worker_state() -> tuple[Any, Any]:
        if not hasattr(local, "pair"):
            compiled = compile_durable()
            with built_lock:
                built.append(compiled)
            local.pair = (compiled, connector_factory())
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
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # `as_completed`, not `pool.map`: `map` yields in *input* order, so one hung provider
            # request holds back every finished row behind it and the crash-safe writer goes
            # quiet. The returned list is still ordered -- callers index it -- but `on_row` now
            # fires when the row is actually done.
            futures = [pool.submit(run_index, i) for i in range(len(questions))]
            for future in as_completed(futures):
                index, row = future.result()
                results[index] = row
                if on_row is not None:
                    on_row(index, row)
    finally:
        # Not housekeeping: an unclosed `aiosqlite` connection holds a non-daemon thread, and
        # CPython joins those *before* `atexit`, so an arm that leaves one open never exits.
        for compiled in built:
            close = getattr(compiled, "close", None)
            if close is not None:
                close()
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
