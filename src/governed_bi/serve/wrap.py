"""Node exception wrapper — every failure routes through ``stamp`` (ADR 0005 §3.1), and
every rail reports itself to the live stream (ADR 0010 §1).

**Why the stream emitter is here and not in the nodes.** Every node is already wrapped, so one
emitter covers every rail, derives its ``step`` from the name the graph registered, and cannot
drift per node. Twenty hand-placed ``writer(...)`` calls is twenty places to forget one, and the
missing-call failure mode is a step that silently never appears in the timeline.

**The two things this wrapper cannot see**, and which therefore emit for themselves: the tools,
which run inside the nested ``create_agent`` graph (``serve/tools.py``), and ``stamp``, which is
deliberately never wrapped (``graph.py``).

**This is also where the turn's clock starts** (audit §10). ``latency_sec`` was a declared
record field with zero writers, and no clock was read anywhere in ``src/governed_bi`` at all —
no ``perf_counter``, no ``monotonic``, no ``time.time()``. Latency was not merely unrecorded;
it had never been measured. The wrapper is the right home because every rail passes through it,
so "when did this turn begin" has one answer rather than one per entry point: the graph starts
at ``accept`` on the served path and at ``guard`` on the CLI and eval paths, and a stamp in
each would be two clocks that drift.
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


def wrap_node(stage: str, fn: Callable[..., dict[str, Any]], *, stream: bool = True):
    """Wrap a node: on exception return ``failure`` + ``path_kind='crashed'``.

    Does not re-raise ordinary exceptions. ``GraphInterrupt`` (HITL ``interrupt()``)
    is re-raised so the checkpointer can pause and resume.

    Forwards ``config`` only when the wrapped function declares a ``config``
    parameter (LangGraph injects ``RunnableConfig``).

    ``stream=False`` suppresses the two stream events. One caller needs it: the ``fanout``
    passthrough is registered under the name ``facet_schema``, so leaving it on emitted a
    phantom ``facet_schema`` row immediately before the real one — two rows for one stage,
    which is worse than a missing one because it looks like the facet ran twice.
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

        Wall clock rather than ``perf_counter``, and that is the point rather than laziness: a
        clarification suspends the turn on a ``GraphInterrupt`` and it can resume in a different
        process, where a ``perf_counter`` reading from the first one means nothing. An epoch
        float survives the checkpoint.

        Written only when absent, so it is the *turn's* start and not the last node's. It is in
        ``PER_TURN_RESET``, so turn two does not inherit turn one's clock and report a latency
        that includes everything the user did in between.
        """
        if state.get("turn_started_at") is not None:
            return {}
        return {"turn_started_at": time.time()}

    is_async = inspect.iscoroutinefunction(fn)

    async def _body(state: Mapping[str, Any], config: RunnableConfig | None) -> dict[str, Any]:
        """Run the node, off the event loop if it is still synchronous.

        **Why ``to_thread`` and not a direct call.** LangGraph runs a sync node in a threadpool,
        so two turns on the server interleave. An ``async def`` wrapper around a still-blocking
        body would hold the loop for the whole of an 8-call turn and serialise them — a
        concurrency regression bought with a refactor that was supposed to be neutral. Nodes
        move to native ``async`` one at a time; until one does, this keeps its old scheduling.
        """
        if is_async:
            return await (fn(state, config) if accepts_config else fn(state))
        update = await (
            asyncio.to_thread(fn, state, config)
            if accepts_config
            else asyncio.to_thread(fn, state)
        )
        # Named here rather than awaited. A sync node returning a coroutine is always a
        # mistake — usually a test double or a decorator that wrapped an async node without
        # becoming one — and awaiting it would hide that. Left alone it surfaces four frames
        # away as ``'coroutine' object has no attribute 'get'`` inside ``rail_observation``,
        # which says nothing about the node that caused it.
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
                # No resolve event. The node has not ended — it is suspended, and the row
                # stays `running`, which is what the interface should show while a human is
                # being asked something. `ask_user` emits its own pair around the pause.
                raise
            except Exception as e:
                update = _crashed(e)
            if live:
                _end(state, update)
            # The clock goes on the update even when the node crashed: a turn that died still
            # took time, and `latency_sec` on a crashed turn is the number that says how long
            # the user waited to be told nothing.
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
