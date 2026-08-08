"""Node exception wrapper — every failure routes through ``stamp`` (ADR 0005 §3.1), and
every rail reports itself to the live stream (ADR 0010 §1).

One emitter here rather than a ``writer(...)`` call per node: it covers every rail and derives
its ``step`` from the name the graph registered, so no node can be forgotten. The two things it
cannot see emit for themselves — the tools, which run inside the nested ``create_agent`` graph
(``serve/tools.py``), and ``stamp``, which is never wrapped (``graph.py``).

Also where the turn's clock starts, so ``turn_started_at`` has one answer rather than one per
entry point (the graph starts at ``accept`` on the served path, ``guard`` on the CLI and eval
paths, and a stamp in each would be two clocks that drift).
"""

import asyncio
import inspect
import time
from collections.abc import Callable, Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt

from governed_bi.serve.events import (
    FIRST_STAGE,
    emit,
    rail_event_id,
    rail_observation,
    silenced_by_terminal_state,
)

__all__ = ["wrap_node"]


def wrap_node(
    stage: str,
    fn: Callable[..., dict[str, Any]],
    *,
    stream: bool = True,
    timeout: float | None = None,
):
    """Wrap a node: on exception return ``failure`` + ``path_kind='crashed'``.

    Does not re-raise ordinary exceptions. ``GraphInterrupt`` (HITL ``interrupt()``) is
    re-raised so the checkpointer can pause and resume. Forwards ``config`` only when the
    wrapped function declares a ``config`` parameter (LangGraph injects ``RunnableConfig``).

    ``stream=False`` suppresses the two stream events. The ``fanout`` passthrough needs it: it
    is registered under the name ``facet_schema``, so leaving it on emits a phantom row
    immediately before the real one, which reads as the facet having run twice.

    ``timeout`` is enforced here rather than via ``add_node(..., timeout=...)``: measured on
    langgraph 1.2.10, that bound fires outside the node function and its ``error_handler`` runs
    *without saving the run* under ``stream_eager`` / ``subgraphs=True`` / ``"messages"`` /
    ``"custom"`` (``pregel/_executor.py`` re-raises at teardown), three of which the served
    surface uses at once; it also left the rail ``running`` forever. Inside, a timeout is an
    ordinary ``TimeoutError``: caught, stamped ``crashed``, resolved on the stream.
    """

    accepts_config = "config" in inspect.signature(fn).parameters
    serve_path = "agent" if stage == FIRST_STAGE else None

    def _start(state: Mapping[str, Any]) -> bool:
        """Announce entry, and report whether this node's events are live at all."""
        if not stream or silenced_by_terminal_state(stage, state):
            return False
        emit(
            kind="rail",
            step=stage,
            status="start",
            event_id=rail_event_id(stage, state),
            serve_path=serve_path,
        )
        return True

    def _end(state: Mapping[str, Any], update: dict[str, Any]) -> None:
        status, detail = rail_observation(stage, update)
        emit(
            kind="rail",
            step=stage,
            status=status,
            event_id=rail_event_id(stage, state),
            detail=detail,
        )

    def _crashed(exc: Exception) -> dict[str, Any]:
        return {
            "failure": {"stage": stage, "error_type": type(exc).__name__},
            "path_kind": "crashed",
        }

    def _started(state: Mapping[str, Any]) -> dict[str, Any]:
        """``{"turn_started_at": <epoch>}`` from the first node of the turn to run, else ``{}``.

        Wall clock rather than ``perf_counter``: a clarification suspends the turn on a
        ``GraphInterrupt`` and can resume in a different process. Written only when absent, so
        it is the *turn's* start; and in ``PER_TURN_RESET``, so turn two does not inherit turn
        one's clock.
        """
        if state.get("turn_started_at") is not None:
            return {}
        return {"turn_started_at": time.time()}

    is_async = inspect.iscoroutinefunction(fn)

    if timeout is not None and not is_async:
        # Cancelling the ``await`` around ``to_thread`` does not stop the thread, so the bound
        # would report a stop it did not perform. Refused loudly at build time.
        raise ValueError(
            f"node {stage!r} is sync and cannot carry a timeout: cancelling the await around "
            "asyncio.to_thread leaves the thread running, so the bound would be a claim rather "
            "than a fact. Make the node `async def` first."
        )

    async def _body(state: Mapping[str, Any], config: RunnableConfig | None) -> dict[str, Any]:
        """Run the node, off the event loop if it is still synchronous.

        ``to_thread`` rather than a direct call: LangGraph runs a sync node in a threadpool, so
        an ``async def`` wrapper around a still-blocking body would hold the loop for a whole
        turn and serialise concurrent turns. Nodes move to native ``async`` one at a time.
        """
        if is_async:
            call = fn(state, config) if accepts_config else fn(state)
            if timeout is not None:
                # `wait_for` cancels the inner coroutine — the reason the sync case is refused
                # above — and raises an ordinary `TimeoutError`, which the `except` below stamps
                # `crashed` like any other.
                return await asyncio.wait_for(call, timeout)
            return await call
        update = await (
            asyncio.to_thread(fn, state, config)
            if accepts_config
            else asyncio.to_thread(fn, state)
        )
        # A sync node returning a coroutine is always a mistake, and awaiting it would hide
        # that. Left alone it surfaces four frames away as ``'coroutine' object has no
        # attribute 'get'`` inside ``rail_observation``, naming nothing.
        if inspect.isawaitable(update):
            update.close()
            raise TypeError(
                f"node {stage!r} is a sync function that returned an awaitable. It is probably "
                "wrapping an async node without awaiting it; make the wrapper `async def`."
            )
        return update

    if accepts_config:

        async def inner(
            state: Mapping[str, Any], config: RunnableConfig
        ) -> dict[str, Any]:
            live = _start(state)
            began = _started(state)
            try:
                update = await _body(state, config)
            except GraphInterrupt:
                # No resolve event: the node is suspended, not ended, so the row stays
                # `running`. `ask_user` emits its own pair around the pause.
                raise
            except Exception as e:
                update = _crashed(e)
            if live:
                _end(state, update)
            # The clock rides the update even on a crash: `latency_sec` on a crashed turn is
            # how long the user waited to be told nothing.
            return {**began, **update}

        return inner

    async def inner_state_only(state: Mapping[str, Any]) -> dict[str, Any]:
        live = _start(state)
        began = _started(state)
        try:
            update = await _body(state, None)
        except GraphInterrupt:
            raise
        except Exception as e:
            update = _crashed(e)
        if live:
            _end(state, update)
        return {**began, **update}

    return inner_state_only
