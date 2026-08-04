"""Node exception wrapper — every failure routes through ``stamp`` (ADR 0005 §3.1), and
every rail reports itself to the live stream (ADR 0010 §1).

**Why the stream emitter is here and not in the nodes.** Every node is already wrapped, so one
emitter covers every rail, derives its ``step`` from the name the graph registered, and cannot
drift per node. Twenty hand-placed ``writer(...)`` calls is twenty places to forget one, and the
missing-call failure mode is a step that silently never appears in the timeline.

**The two things this wrapper cannot see**, and which therefore emit for themselves: the tools,
which run inside the nested ``create_agent`` graph (``serve/tools.py``), and ``stamp``, which is
deliberately never wrapped (``graph.py``).
"""

import inspect
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

    if accepts_config:

        def inner(
            state: Mapping[str, Any], config: RunnableConfig
        ) -> dict[str, Any]:
            live = _start(state)
            try:
                update = fn(state, config)
            except GraphInterrupt:
                # No resolve event. The node has not ended — it is suspended, and the row
                # stays `running`, which is what the interface should show while a human is
                # being asked something. `ask_user` emits its own pair around the pause.
                raise
            except Exception as e:
                update = _crashed(e)
            if live:
                _end(state, update)
            return update

        return inner

    def inner_state_only(state: Mapping[str, Any]) -> dict[str, Any]:
        live = _start(state)
        try:
            update = fn(state)
        except GraphInterrupt:
            raise
        except Exception as e:
            update = _crashed(e)
        if live:
            _end(state, update)
        return update

    return inner_state_only
