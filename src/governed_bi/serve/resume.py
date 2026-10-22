"""Resuming a paused ``ask_user`` interrupt in process (ADR 0005 HITL)."""

from __future__ import annotations

from typing import Any, Mapping

from langgraph.types import Command

__all__ = ["resume_clarification"]


def resume_clarification(graph: Any, *, config: Mapping[str, Any], answer: Any) -> Any:
    """The thread id on ``config`` is the only handle for the pending interrupt."""
    return graph.invoke(Command(resume=answer), dict(config))
