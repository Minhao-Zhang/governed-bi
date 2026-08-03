"""Identity-bound clarification resume (ADR 0006 B9 / ADR 0005 HITL)."""

from __future__ import annotations

from typing import Any, Mapping

from langgraph.types import Command

from governed_bi.govern.bounds import resume_authorised
from governed_bi.serve.runtime import configurable

__all__ = ["ResumeRejected", "resume_clarification", "identity_token"]


class ResumeRejected(PermissionError):
    """Caller identity does not match the paused turn's identity."""


def identity_token(identity: Any) -> str | None:
    """Reduce ``state.identity`` (str or mapping) to the compare token."""
    if identity is None:
        return None
    if isinstance(identity, str):
        return identity or None
    if isinstance(identity, Mapping):
        for key in ("token", "id", "subject", "user_id"):
            raw = identity.get(key)
            if raw:
                return str(raw)
        return None
    return str(identity) or None


def resume_clarification(
    graph: Any,
    *,
    config: Mapping[str, Any],
    identity: Any,
    answer: Any,
) -> Any:
    """Resume an ``ask_user`` interrupt after ``resume_authorised`` succeeds.

    Loads the checkpointed ``identity`` for this ``thread_id`` and compares it to
    ``identity`` with :func:`~governed_bi.govern.bounds.resume_authorised`. On
    mismatch raises :class:`ResumeRejected` and does **not** issue ``Command(resume=)``.
    """
    cfg = dict(config)
    conf = dict(configurable(cfg))
    cfg["configurable"] = conf

    stored = _stored_identity(graph, cfg)
    caller = identity_token(identity)
    if not resume_authorised(stored_identity=stored, caller_identity=caller):
        raise ResumeRejected("resume identity mismatch")
    return graph.invoke(Command(resume=answer), cfg)


def _stored_identity(graph: Any, config: Mapping[str, Any]) -> str | None:
    snap = graph.get_state(config)
    values = getattr(snap, "values", None) or {}
    if isinstance(values, Mapping):
        return identity_token(values.get("identity"))
    return None
