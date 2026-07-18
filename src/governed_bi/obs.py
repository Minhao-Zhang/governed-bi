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

import logging
import os
from typing import Any

logger = logging.getLogger("governed_bi.obs")

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


def _trace_mask(*, data: Any, **_: Any) -> Any:
    """Truncate long string values before they reach the external tracer (LF2).

    The Langfuse callback auto-captures full run inputs/outputs — including
    ``run_query`` / ``sample_rows`` tool messages that carry live DB row previews and
    the governed context. A governed BI product should not ship that verbatim to a
    third party, so long strings (where row/context dumps live) are truncated. Set
    ``GOVERNED_BI_TRACE_MAX_CHARS`` to tune (default 300; 0 disables masking).
    """
    try:
        limit = int(os.environ.get("GOVERNED_BI_TRACE_MAX_CHARS", "300"))
    except ValueError:
        limit = 300
    if limit <= 0:
        return data

    def _m(value: Any) -> Any:
        if isinstance(value, str):
            if len(value) <= limit:
                return value
            return value[:limit] + f"…[+{len(value) - limit} chars redacted]"
        if isinstance(value, dict):
            return {k: _m(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_m(v) for v in value]
        return value

    try:
        return _m(data)
    except Exception:
        return data


def _langfuse_handler() -> Any | None:
    """A Langfuse LangChain callback handler, or None when unavailable/unconfigured.

    Configures the Langfuse client with a redaction ``mask`` (LF2) before building
    the handler. Failures are logged (not silently swallowed, LF3) so a
    keys-set-but-no-traces misconfiguration is diagnosable.
    """
    if not _langfuse_configured():
        logger.debug("Langfuse not configured (LANGFUSE_PUBLIC_KEY/SECRET_KEY unset)")
        return None
    try:  # langfuse v3 exposes the handler here; v2 under langfuse.callback
        from langfuse.langchain import CallbackHandler
    except Exception:
        try:
            from langfuse.callback import CallbackHandler
        except Exception:
            logger.warning("LANGFUSE_* set but the Langfuse CallbackHandler could not be imported")
            return None
    try:  # apply the redaction mask to the singleton client (v3); harmless if absent
        from langfuse import Langfuse

        Langfuse(mask=_trace_mask)
    except Exception:
        logger.debug("could not configure a Langfuse mask; traces will be unmasked", exc_info=True)
    try:
        return CallbackHandler()
    except Exception:
        logger.warning("LANGFUSE_* set but the Langfuse handler failed to construct", exc_info=True)
        return None


def tracing_callbacks() -> list:
    """LangChain callbacks for external tracing (Langfuse).

    Empty when the ``tracing`` extra is not installed or the keys are unset, so it
    is safe to splice into any ``config={"callbacks": ...}`` unconditionally.
    LangSmith is not included here; it instruments itself from the environment.
    """
    handler = _langfuse_handler()
    return [handler] if handler is not None else []


def flush_tracing() -> None:
    """Flush pending external traces (safe no-op when unconfigured) — LF1.

    The Langfuse v3 SDK exports on a background thread and relies on an ``atexit``
    hook that SIGTERM / ``os._exit`` / CI cancellation bypass, dropping the final
    batch. Short-lived processes (eval, curator, CLI) call this before exit so
    traces are delivered deterministically regardless of exit path.
    """
    if not _langfuse_configured():
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception:
        logger.debug("Langfuse flush failed", exc_info=True)
