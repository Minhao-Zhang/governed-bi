"""Node exception wrapper — every failure routes through ``stamp`` (ADR 0005 §3.1)."""

import inspect
from collections.abc import Callable, Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt

__all__ = ["wrap_node"]


def wrap_node(stage: str, fn: Callable[..., dict[str, Any]]):
    """Wrap a node: on exception return ``failure`` + ``path_kind='crashed'``.

    Does not re-raise ordinary exceptions. ``GraphInterrupt`` (HITL ``interrupt()``)
    is re-raised so the checkpointer can pause and resume.

    Forwards ``config`` only when the wrapped function declares a ``config``
    parameter (LangGraph injects ``RunnableConfig``).
    """

    accepts_config = "config" in inspect.signature(fn).parameters

    if accepts_config:

        def inner(
            state: Mapping[str, Any], config: RunnableConfig
        ) -> dict[str, Any]:
            try:
                return fn(state, config)
            except GraphInterrupt:
                raise
            except Exception as e:
                return {
                    "failure": {"stage": stage, "error_type": type(e).__name__},
                    "path_kind": "crashed",
                }

        return inner

    def inner_state_only(state: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return fn(state)
        except GraphInterrupt:
            raise
        except Exception as e:
            return {
                "failure": {"stage": stage, "error_type": type(e).__name__},
                "path_kind": "crashed",
            }

    return inner_state_only
