"""Observability wiring: LangSmith (native, env-driven) + Langfuse (callback).

Two tracers, both opt-in by environment and both no-ops when unset:

- **LangSmith** needs no code here. Set ``LANGSMITH_TRACING=true`` (or the legacy
  ``LANGCHAIN_TRACING_V2=true``) and ``LANGSMITH_API_KEY`` and LangChain/LangGraph
  emit traces automatically, so the whole chat run (under ``langgraph dev`` /
  Platform) and every model call are traced with zero wiring. This is the
  LangChain-native path.
- **Langfuse** is attached as a LangChain callback via :func:`tracing_callbacks`,
  returned only when the ``tracing`` extra is installed *and* the ``LANGFUSE_*``
  keys are set. It is spliced into ``config={"callbacks": ...}`` at each run
  boundary: the agentic serve rails (``analyst.agent`` — outer ``graph.invoke`` +
  the inner ``agent.stream``) and the curator/SME deep agents thread it into
  their invoke config. Callbacks passed at the outer ``graph.invoke`` propagate to
  child runs, so an agentic turn groups as one Langfuse trace.
  ``LangChainChatClient.complete`` inherits that active run's callbacks when called
  from inside a graph node (the serve-path narrator + schema router), so those
  model calls nest under the same turn's trace instead of opening a new root; it
  only attaches its own handler when invoked standalone (eval baseline, curator).

Nothing here imports langfuse at module load, so the base install and the offline
profile are unaffected. See ``.env.example`` for the variable names.
"""

from __future__ import annotations

import os
from typing import Any

_TRUTHY = {"1", "true", "yes", "on"}


def _env_truthy(*names: str) -> bool:
    """True if any named env var is set to a truthy value."""
    for name in names:
        if os.environ.get(name, "").strip().lower() in _TRUTHY:
            return True
    return False


def langsmith_enabled() -> bool:
    """Whether LangSmith tracing is turned on by the environment.

    Accepts ``LANGSMITH_TRACING`` (current LangSmith docs) or the legacy
    ``LANGCHAIN_TRACING_V2``, plus ``LANGSMITH_API_KEY``.
    """
    tracing = _env_truthy("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")
    return tracing and bool(os.environ.get("LANGSMITH_API_KEY"))


def _langfuse_configured() -> bool:
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
    )


def _langfuse_handler() -> Any | None:
    """A Langfuse LangChain callback handler, or None when unavailable/unconfigured."""
    if not _langfuse_configured():
        return None
    try:  # langfuse v3 exposes the handler here; v2 under langfuse.callback
        from langfuse.langchain import CallbackHandler
    except Exception:
        try:
            from langfuse.callback import CallbackHandler
        except Exception:
            return None
    try:
        return CallbackHandler()
    except Exception:
        return None


def tracing_callbacks() -> list:
    """LangChain callbacks for external tracing (Langfuse).

    Empty when the ``tracing`` extra is not installed or the keys are unset, so it
    is safe to splice into any ``config={"callbacks": ...}`` unconditionally.
    LangSmith is not included here; it instruments itself from the environment.
    """
    handler = _langfuse_handler()
    return [handler] if handler is not None else []
