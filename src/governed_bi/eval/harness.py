"""Eval harness — invoke serve ``compile_durable`` per question (ADR 0005 §4.1)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from governed_bi.eval.configs import ConfigSpec, model_for_question
from governed_bi.eval.oracle import oracle_grade
from governed_bi.eval.projection import project_turn
from governed_bi.eval.replay import PINNED_SCHEMAS_KEY
from governed_bi.registry.stages import Outcome
from governed_bi.registry.stored_names import artifact_get
from governed_bi.serve.graph import compile_durable

__all__ = ["EvictionWouldHang", "run_config", "run_comparison", "project_turn"]


class EvictionWouldHang(RuntimeError):
    """The only eviction left would block forever. A hang writes no rows and no traceback."""


def run_config(
    questions: Sequence[Mapping[str, Any]],
    config: ConfigSpec,
    *,
    order_sensitive_qids: frozenset[str] | None = None,
    graph: Any | None = None,
    run_id: str | None = None,
    session: Any | None = None,
    workers: int = 1,
    connector_factory: Any | None = None,
    model_factory: Any | None = None,
    on_row: Any | None = None,
) -> list[dict[str, Any]]:
    """In input order. A fabricated corpus hash equates different corpora. Each worker needs its own connector, or a
    mixed result looks like a wrong answer."""
    order_sensitive_qids = order_sensitive_qids or frozenset()
    run_id = run_id or f"run-{uuid4().hex[:12]}"
    base_cfg = config.build_configurable()

    if config.oracle_only:
        connector = base_cfg.get("connector")
        if connector is None:
            raise ValueError("oracle config requires connector in configurable")
        # Row by row so ``on_row`` runs. One unexecutable gold must not drop rows already computed.
        rows: list[dict[str, Any]] = []
        for index, question in enumerate(questions):
            row = oracle_grade(question, connector, order_sensitive_qids=order_sensitive_qids)
            row["config"] = config.name
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
        # A caller-supplied graph is the caller's to close. Durable keeps threads out of the conversation store.
        compiled = graph if graph is not None else compile_durable()
        ours = graph is None
        out: list[dict[str, Any]] = []
        try:
            for index, question in enumerate(questions):
                row = _run_one(
                    question,
                    config=config,
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
            # A caller-supplied graph need not be closeable. Closing must not become the config's error.
            if ours:
                close = getattr(compiled, "close", None)
                if close is not None:
                    close()
        return out

    return _run_concurrently(
        questions,
        config=config,
        base_cfg=base_cfg,
        session=session,
        run_id=run_id,
        order_sensitive_qids=order_sensitive_qids,
        workers=workers,
        connector_factory=connector_factory,
        model_factory=model_factory,
        on_row=on_row,
    )


def _run_one(
    question: Mapping[str, Any],
    *,
    config: ConfigSpec,
    base_cfg: Mapping[str, Any],
    compiled: Any,
    session: Any,
    run_id: str,
    order_sensitive_qids: frozenset[str],
    connector: Any | None = None,
    models: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One question through to a graded row, for both the serial and concurrent paths."""
    qid = str(question["question_id"])
    conf = dict(base_cfg)
    if connector is not None:
        conf["connector"] = connector
    conf.update(models or {})
    model = model_for_question(conf, qid)
    if model is not None:
        conf["agent_model"] = model
    conf.pop("scripted_sql_lookup", None)
    thread_id = f"{run_id}-{config.name}-{qid}"
    conf["thread_id"] = thread_id

    if session is not None:
        # The session mints ids. The eval question id stays on the row: `Session.turn`
        # digests the question text, so a re-serve stays recognisable.
        turn = session.turn(
            str(question.get("question") or question.get("utterance") or qid),
            turn_index=1,
            thread_id=thread_id,
            # Evidence is the benchmark condition. Omitting the key is the no-hint condition.
            evidence=question.get("evidence"),
        )
        # `Session.turn` writes its own settings. A per-question override would
        # otherwise be replaced, and the run would serve the default under a
        # header that announced the override.
        settings = _turn_settings(question, session)
        if settings is not None:
            turn["settings_resolved"] = settings
    else:
        turn = _base_turn(question, run_id=run_id, config=config.name)
    # No index: inject a route hit so the answered path can reach the agent.
    if conf.get("index") is None and "channel_route_hits" not in turn:
        db = str(question.get("db_id") or "fixture")
        turn["channel_route_hits"] = [("channel_schema", db, 1.0)]

    # After `Session.turn`, which resets this channel. Absent means the question
    # routes live and is counted as unpinned.
    if question.get(PINNED_SCHEMAS_KEY):
        turn[PINNED_SCHEMAS_KEY] = list(question[PINNED_SCHEMAS_KEY])

    result = compiled.invoke(turn, {"configurable": conf})
    row = project_turn(
        result,
        question=question,
        config=config.name,
        order_sensitive=qid in order_sensitive_qids,
        connector=conf.get("connector"),
    )
    row["run_id"] = run_id
    _evict(compiled, thread_id)
    return row


def _turn_settings(question: Mapping[str, Any], session: Any) -> dict[str, Any] | None:
    """``None`` when neither has settings. ``{}`` would read as a configuration whose every setting is missing."""
    for source in (artifact_get(question, "settings_resolved"), getattr(session, "settings_resolved", None)):
        if isinstance(source, Mapping):
            return dict(source)
    return None


def _evict(compiled: Any, thread_id: str) -> None:
    """After the row is projected. A failed eviction is a leak. A sync delete that would block forever raises."""
    saver = getattr(getattr(compiled, "_app", compiled), "checkpointer", None)
    if saver is None:
        return
    # Prefer the async delete on the graph's own loop: that is the loop its connection answers.
    run_coro = getattr(compiled, "run_coro", None)
    adelete = getattr(saver, "adelete_thread", None)
    if run_coro is not None and adelete is not None and getattr(compiled, "_loop", None) is not None:
        try:
            run_coro(adelete(thread_id))
        except Exception:  # noqa: BLE001 — a saver that cannot evict is a leak, not a failed turn
            return
        return
    # No config reaches this. On a loop-bound saver, sync `delete_thread` blocks
    # forever and does not raise. Refuse it by name: `except Exception` cannot
    # catch a hang.
    if getattr(saver, "loop", None) is not None:
        raise EvictionWouldHang(
            f"{type(saver).__name__} carries its own event loop, so its synchronous "
            f"`delete_thread` bridges onto that loop and blocks forever when it is not running "
            f"-- which is this harness between calls. The async `adelete_thread` is the only "
            f"usable eviction and it was not reachable (adelete_thread="
            f"{adelete is not None}, run_coro={run_coro is not None}, "
            f"pinned_loop={getattr(compiled, '_loop', None) is not None}). Failing here rather "
            f"than hanging: a config that blocks on question one writes no rows and no traceback."
        )
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
    config: ConfigSpec,
    base_cfg: Mapping[str, Any],
    session: Any,
    run_id: str,
    order_sensitive_qids: frozenset[str],
    workers: int,
    connector_factory: Any,
    on_row: Any | None,
    model_factory: Any | None = None,
) -> list[dict[str, Any]]:
    """One graph and one connector per worker. A raised question is a crashed row, not a shorter file."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    local = threading.local()
    # Every graph this pool builds, closed in `finally`. Each worker needs its
    # own graph: a shared connection answers only the loop that opened it.
    built: list[Any] = []
    #: ``(graph, aclose)`` per worker: a client's sockets must close on its own loop, or that loop never closes.
    closers: list[tuple[Any, Any]] = []
    built_lock = threading.Lock()

    def worker_state() -> tuple[Any, Any, Mapping[str, Any]]:
        if not hasattr(local, "state"):
            compiled = compile_durable()
            with built_lock:
                built.append(compiled)
            # Each worker runs its own event loop, and an async model client binds to the first loop it meets.
            models = dict(model_factory()) if model_factory is not None else {}
            aclose = models.pop("aclose", None)
            if aclose is not None:
                with built_lock:
                    closers.append((compiled, aclose))
            local.state = (compiled, connector_factory(), models)
        return local.state

    def run_index(index: int) -> tuple[int, dict[str, Any]]:
        compiled, connector, models = worker_state()
        question = questions[index]
        try:
            row = _run_one(
                question,
                config=config,
                base_cfg=base_cfg,
                compiled=compiled,
                session=session,
                run_id=run_id,
                order_sensitive_qids=order_sensitive_qids,
                connector=connector,
                models=models,
            )
        except Exception as err:  # noqa: BLE001 — one bad question must not end the config
            row = {
                "question_id": str(question.get("question_id")),
                "config": config.name,
                "run_id": run_id,
                # Settings and the gold schema are known even when the turn did not
                # finish. Omitting them makes one crash fail the settings check
                # and drop the row from routing.
                "settings_resolved": _turn_settings(question, session),
                "db_id": question.get("db_id"),
                "outcome": Outcome.crashed.value,
                "correct": False,
                "crashed": True,
                "quality_flags": list(question.get("quality_flags") or ()),
                "error_type": type(err).__name__,
                "grade_detail": f"harness: {type(err).__name__}: {err}",
                "retrieved_tables": [],
                "schemas": [],
                "usage": [],
            }
        return index, row

    results: dict[int, dict[str, Any]] = {}
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # `as_completed`, not `map`. `map` yields in input order, so one hung
            # request holds back finished rows and the writer stalls. The
            # returned list is still ordered.
            futures = [pool.submit(run_index, i) for i in range(len(questions))]
            for future in as_completed(futures):
                index, row = future.result()
                results[index] = row
                if on_row is not None:
                    on_row(index, row)
    finally:
        for compiled, aclose in closers:
            try:
                compiled.run_coro(aclose())
            except Exception:  # noqa: BLE001 -- a client that will not close is not a row
                pass
        # An unclosed connection holds a non-daemon thread, and the process waits for it.
        for compiled in built:
            close = getattr(compiled, "close", None)
            if close is not None:
                close()
    return [results[i] for i in range(len(questions))]


def run_comparison(
    questions: Sequence[Mapping[str, Any]],
    configs: Sequence[ConfigSpec],
    *,
    order_sensitive_qids: frozenset[str] | None = None,
    run_id: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    run_id = run_id or f"run-{uuid4().hex[:12]}"
    return {
        config.name: run_config(
            questions,
            config,
            order_sensitive_qids=order_sensitive_qids,
            run_id=run_id,
        )
        for config in configs
    }


def _base_turn(question: Mapping[str, Any], *, run_id: str, config: str) -> dict[str, Any]:
    qid = str(question["question_id"])
    db = str(question.get("db_id") or "fixture")
    return {
        "question": str(question.get("question") or question.get("utterance") or qid),
        "thread_id": f"{run_id}-{config}-{qid}",
        "turn_index": 1,
        "run_id": run_id,
        "turn_id": f"turn-{config}-{qid}",
        "question_id": qid,
        "db_id": db,
        "evidence": str(question.get("evidence") or ""),
        "attempt_id": f"attempt-{config}-{qid}",
        "corpus_content_hash": str(question.get("corpus_content_hash") or f"corpus-{config}"),
        "prompt_set_hash": str(question.get("prompt_set_hash") or "prompt-eval"),
        "settings_resolved": dict(question.get("settings_resolved") or {"route_top_n": 3}),
        "n_re_served": 0,
        "messages": [],
        "usage": [],
        "clarifications": [],
    }
