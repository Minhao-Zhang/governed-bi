"""Every failure routes through ``finish``, and every rail reports itself (ADR 0005 §3.1).

The turn clock starts here: the graph enters at ``accept`` or at ``guard``, and a clock
in each would drift. Tools inside ``create_agent``, and ``finish``, emit for themselves (ADR 0010 §1)."""

import asyncio
import inspect
import logging
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

_log = logging.getLogger(__name__)


def _log_crash(stage: str, state: Mapping[str, Any]) -> None:
    """The traceback, for the operator. The turn record keeps only the exception's type."""
    _log.exception(
        "node %s crashed (thread %s, turn %s)", stage, state.get("thread_id"), state.get("turn_id")
    )


def _turn_clock(
    state: Mapping[str, Any], update: Mapping[str, Any], entered_at: float
) -> dict[str, Any]:
    """A null in the update starts a new turn. A later node must not keep turn one's clock."""
    if "turn_started_at" in update:
        # Membership before value: present-and-null and absent differ. A sentinel would collide with `_ABSENT`.
        return {} if update["turn_started_at"] is not None else {"turn_started_at": entered_at}
    if state.get("turn_started_at") is None:
        return {"turn_started_at": entered_at}
    return {}


def wrap_node(
    stage: str,
    fn: Callable[..., Any],
    *,
    stream: bool = True,
    timeout: float | None = None,
):
    """Return ``path_kind='crashed'`` and re-raise ``GraphInterrupt``.

    ``stream=False`` skips a phantom row. On langgraph 1.2.10 ``add_node``'s timeout leaves the rail ``running``."""

    accepts_config = "config" in inspect.signature(fn).parameters
    serve_path = "agent" if stage == FIRST_STAGE else None

    def _start(state: Mapping[str, Any]) -> bool:
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

    def _end(state: Mapping[str, Any], update: Mapping[str, Any]) -> None:
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

    def _entered_at() -> float:
        """Wall time. ``perf_counter`` means nothing when a clarification resumes in another process."""
        return time.time()

    is_async = inspect.iscoroutinefunction(fn)

    if timeout is not None and not is_async:
        # Cancelling the await around ``to_thread`` does not stop the thread, so a sync node cannot take a timeout.
        raise ValueError(
            f"node {stage!r} is sync and cannot carry a timeout: cancelling the await around "
            "asyncio.to_thread leaves the thread running, so the bound would be a claim rather "
            "than a fact. Make the node `async def` first."
        )

    async def _body(state: Mapping[str, Any], config: RunnableConfig | None) -> Mapping[str, Any]:
        """Off the loop when still synchronous. Awaiting a blocking body would serialise concurrent turns."""
        if is_async:
            call = fn(state, config) if accepts_config else fn(state)
            if timeout is not None:
                # ``wait_for`` cancels the inner coroutine and raises ``TimeoutError``, finished as ``crashed``.
                update = await asyncio.wait_for(call, timeout)
            else:
                update = await call
        else:
            update = await (
                asyncio.to_thread(fn, state, config)
                if accepts_config
                else asyncio.to_thread(fn, state)
            )
            # Raise here. Left alone, the failure surfaces inside ``rail_observation`` as a missing ``get``.
            if inspect.isawaitable(update):
                # ``close`` avoids a never-awaited warning. Only a coroutine has it.
                close = getattr(update, "close", None)
                if callable(close):
                    close()
                raise TypeError(
                    f"node {stage!r} is a sync function that returned an awaitable. It is "
                    "probably wrapping an async node without awaiting it; make the wrapper "
                    "`async def`."
                )
        # Inside the try: a non-mapping must become ``crashed``, which is what this wrapper is for.
        if not isinstance(update, Mapping):
            raise TypeError(
                f"node {stage!r} returned {type(update).__name__}, not a mapping. A LangGraph "
                "node returns a partial state dict; returning None is not 'no update'."
            )
        return update

    if accepts_config:

        async def inner(
            state: Mapping[str, Any], config: RunnableConfig
        ) -> dict[str, Any]:
            live = _start(state)
            entered_at = _entered_at()
            try:
                update = await _body(state, config)
            except GraphInterrupt:
                # No resolve event: the node is suspended, so the row stays ``running``.
                raise
            except Exception as e:  # noqa: BLE001 — becomes the turn's crashed finish
                _log_crash(stage, state)
                update = _crashed(e)
            if live:
                _end(state, update)
            # Written even on a crash: ``latency_sec`` is how long the user waited.
            return {**update, **_turn_clock(state, update, entered_at)}

        return inner

    async def inner_state_only(state: Mapping[str, Any]) -> dict[str, Any]:
        live = _start(state)
        entered_at = _entered_at()
        try:
            update = await _body(state, None)
        except GraphInterrupt:
            raise
        except Exception as e:  # noqa: BLE001 — becomes the turn's crashed finish
            _log_crash(stage, state)
            update = _crashed(e)
        if live:
            _end(state, update)
        return {**update, **_turn_clock(state, update, entered_at)}

    return inner_state_only
