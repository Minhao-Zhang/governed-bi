"""Server-side append of ``ServeState.raised`` without ``threads.update``.

``POST /threads/{id}/state`` is denied (A2/A3): that path runs
``handle_event("update")`` and 403s. The saver-less Pregel ``make_graph``
compiles is also not a writer — LangGraph Server attaches the checkpointer
only when ``get_graph`` copies it on. Direct ``aupdate_state`` on that Pregel
raises ``No checkpointer set``, and even a saver-bearing copy would leave the
thread row's ``values`` stale: ``Threads.search`` (pending, trace) reads the
pickle, which ``Threads.set_status`` copies at run completion.

This module is the missing half of official ``Threads.State.post``:
``aupdate_state(as_node="raise_note")`` then ``set_status``. It does not call
``State.post`` or ``handle_event("update")``.

``as_node="raise_note"`` means "raise_note just finished". That node has no
edges, so ``snapshot.next`` becomes ``()``. Filing on a paused thread would
consume the live clarification — hence 409 when ``next`` is set or a run is
in flight.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

__all__ = [
    "InFlightUnknown",
    "ThreadBusy",
    "ThreadNotFound",
    "append_raised_on_thread",
    "file_raised",
]


class ThreadBusy(Exception):
    """Filing a note now would consume a live interrupt or collide with a run."""


class InFlightUnknown(ThreadBusy):
    """The run table could not be read, so "no run is in flight" is not established.

    A subclass of :class:`ThreadBusy` on purpose: the append is refused either way, and
    the route already maps ``ThreadBusy`` to 409, so an unreadable run table degrades to
    "come back later" rather than to a 500 or — the defect this replaces — to a silent
    "not busy". Distinct so a caller that wants to tell "a run is running" from "this
    runtime is not supported" can, and so the message can say which.
    """


class ThreadNotFound(Exception):
    """The thread id is missing from the runtime store, or is not a UUID."""


_Publish = Callable[[Any, UUID, dict[str, Any]], Awaitable[None]]


def append_raised_on_thread(thread_id: str, payload: Mapping[str, Any]) -> None:
    """Drive :func:`file_raised` on the Agent server's main loop.

    Starlette runs a sync ``def`` in a worker with no running loop. The
    checkpointer's locks are bound to ``langgraph_api.asyncio._MAIN_LOOP``, so
    ``asyncio.run`` (a new loop) cannot take them. ``run_coroutine_threadsafe``
    hops onto that loop; ``call_soon_in_main_loop`` cannot — it requires a
    running caller loop.
    """
    _hop(_file_raised_in_server(str(thread_id), dict(payload)))


async def file_raised(
    thread_id: str,
    payload: Mapping[str, Any],
    *,
    graph: Any,
    conn: Any,
    publish: _Publish | None = None,
) -> Any:
    """Append one ``raised`` row on a graph that already carries a checkpointer.

    409s (via :class:`ThreadBusy`) when a run is in flight, when ``_in_flight``
    cannot read this runtime's runs at all (:class:`InFlightUnknown`), or when
    ``snapshot.next`` is non-empty — the two guards cover different things, see
    :func:`_in_flight`. Then ``aupdate_state(as_node="raise_note")``. ``publish`` is
    the ``Threads.set_status`` half that copies ``values`` / ``next`` / interrupt
    tasks onto the thread row pending and trace read.
    """
    tid = _uuid(thread_id)
    if _in_flight(conn, tid):
        raise ThreadBusy(f"thread {thread_id} has an in-flight run")
    config = {
        "configurable": {
            "thread_id": str(thread_id),
            "checkpoint_ns": "",
        }
    }
    snapshot = await graph.aget_state(config)
    if tuple(getattr(snapshot, "next", ()) or ()):
        raise ThreadBusy(f"thread {thread_id} is paused; filing a note would consume the live interrupt")
    await graph.aupdate_state(config, {"raised": [dict(payload)]}, as_node="raise_note")
    state = await graph.aget_state(config)
    if publish is not None:
        await publish(
            conn,
            tid,
            {
                "next": list(state.next),
                "values": state.values,
                "tasks": [
                    {
                        "id": t.id,
                        "interrupts": list(t.interrupts),
                    }
                    for t in state.tasks
                ],
            },
        )
    return state


async def _file_raised_in_server(thread_id: str, payload: dict[str, Any]) -> None:
    """Wire :func:`file_raised` to the running Agent server. Not imported in tests."""
    from langgraph_api.graph import get_graph
    from langgraph_api.store import get_store
    from langgraph_runtime.database import connect
    from langgraph_runtime.ops import Threads, _get_checkpointer

    tid = _uuid(thread_id)
    async with connect() as conn:
        thread = next(
            (row for row in (conn.store.get("threads") or []) if _same_thread(row.get("thread_id"), tid)),
            None,
        )
        if thread is None:
            raise ThreadNotFound(f"thread {thread_id} not found")
        metadata = thread.get("metadata") or {}
        graph_id = metadata.get("graph_id") or "serve"
        thread_config = thread.get("config") or {}
        merged = {
            **thread_config,
            "configurable": {
                **(thread_config.get("configurable") or {}),
                "thread_id": str(thread_id),
                "graph_id": graph_id,
                "checkpoint_ns": "",
            },
        }
        checkpointer = await _get_checkpointer()
        store = await get_store()
        async with get_graph(
            graph_id,
            merged,
            checkpointer=checkpointer,
            store=store,
            access_context="threads.read",
        ) as graph:

            async def _publish(conn_: Any, thread_uuid: UUID, checkpoint: dict[str, Any]) -> None:
                await Threads.set_status(conn_, thread_uuid, checkpoint, None)

            await file_raised(
                thread_id,
                payload,
                graph=graph,
                conn=conn,
                publish=_publish,
            )


def _uuid(thread_id: str) -> UUID:
    try:
        return UUID(str(thread_id))
    except ValueError as exc:
        raise ThreadNotFound(f"thread {thread_id!r} is not a UUID; LangGraph thread ids are") from exc


def _same_thread(stored: Any, thread_id: UUID) -> bool:
    if stored == thread_id:
        return True
    if isinstance(stored, str):
        try:
            return UUID(stored) == thread_id
        except ValueError:
            return False
    return False


def _in_flight(conn: Any, thread_id: UUID) -> bool:
    """True when a run on ``thread_id`` is pending or running. Raises when it cannot tell.

    Reads ``conn.store["runs"]``, which is the ``langgraph-runtime-inmem`` ops store
    (``GlobalStore``, a dict that always initialises ``runs`` to ``[]`` at ``connect``).
    An empty list is therefore a real answer — no runs exist — while a missing ``store``,
    a store with no ``get``, or an absent ``runs`` key means the connection is some other
    runtime (Postgres holds runs in a table, not a pickled dict) and this reader does not
    know how to ask it.

    **Unknown is refused, not waved through.** This used to ``return False`` on any
    connection without a ``.store``, which silently deleted the guard on every runtime but
    one and answered "no run is in flight" without having looked — the worst of the three
    options, because the append then races a live run and nothing says so. Refusing is
    fail-closed and cheap to correct: filing a note is an operator action that can be
    retried, and the loud half is the message, which names the connection type so the fix
    is "teach this function that runtime", not "wonder why 409".

    This is the *collision* half of the guard only. Whether the thread is **paused** is a
    separate check in :func:`file_raised` — ``snapshot.next`` off the graph's own state,
    which works on any checkpointer and is unaffected by this — and that is the one that
    protects a live ``ask_user`` interrupt from being consumed.
    """
    store = getattr(conn, "store", None)
    if store is None or not hasattr(store, "get"):
        raise InFlightUnknown(
            f"cannot tell whether thread {thread_id} has an in-flight run: connection "
            f"{type(conn).__name__} exposes no readable ops store; refusing rather than "
            "assuming the thread is idle"
        )
    runs = store.get("runs")
    if runs is None:
        raise InFlightUnknown(
            f"cannot tell whether thread {thread_id} has an in-flight run: the ops store on "
            f"{type(conn).__name__} has no 'runs' collection; refusing rather than assuming "
            "the thread is idle"
        )
    for run in runs:
        if not isinstance(run, Mapping):
            # Skipping the row would be the same fail-open in miniature: an unreadable run
            # row is not an idle one.
            raise InFlightUnknown(
                f"cannot tell whether thread {thread_id} has an in-flight run: a run row is "
                f"{type(run).__name__}, not a mapping; refusing rather than assuming the "
                "thread is idle"
            )
        if _same_thread(run.get("thread_id"), thread_id) and run.get("status") in ("pending", "running"):
            return True
    return False


def _hop(coro: Any) -> Any:
    try:
        from langgraph_api.asyncio import run_coroutine_threadsafe
    except Exception as exc:  # noqa: BLE001 — config KeyError outside the server
        raise RuntimeError(
            f"filing a raised note only works inside the Agent server ({type(exc).__name__}: {exc})"
        ) from exc
    try:
        return run_coroutine_threadsafe(coro).result()
    except RuntimeError as exc:
        if "No event loop set" in str(exc):
            raise RuntimeError(
                "filing a raised note only works inside the Agent server (the server's main loop is not running)"
            ) from exc
        raise
