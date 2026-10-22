"""Entry node. Provenance comes from the session, so a client-supplied value is not merged.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from governed_bi.serve.state import PER_TURN_RESET

__all__ = ["accept_node"]


def accept_node(session: Any) -> Callable[[dict, Any], dict]:
    """Closes over ``session``: LangGraph Server can only put JSON on configurable. See ADR 0007 §1.
    """

    def accept(state: dict, config: Any) -> dict:
        question = _last_human(state)
        if not question:
            # Clear prior-turn LastValue channels before jumping to finish. Without this (and
            # without the accept→finish edge) guard would still run on the previous question.
            return {
                **PER_TURN_RESET,
                "path_kind": "crashed",
                "failure": {
                    "stage": "accept",
                    "error_type": "ValueError",
                    "detail": "no human message in the conversation",
                },
            }
        prior = sum(1 for m in state.get("messages") or [] if _kind(m) == "human")
        turn = session.turn(
            question,
            turn_index=max(1, prior),
            thread_id=_thread_id(config),
        )
        # Per-turn query vector: streamed path binds config once with no question.
        if session.embedder is not None:
            try:
                turn["query_vector"] = list(session.embedder.embed([question])[0])
            except Exception:  # noqa: BLE001 — a dead embedder must not cost the turn its answer
                pass
        turn.pop("messages", None)
        return turn

    return accept


def _kind(message: Any) -> str:
    return str(getattr(message, "type", "") or (message.get("type", "") if isinstance(message, dict) else ""))


def _last_human(state: Mapping[str, Any]) -> str:
    for message in reversed(state.get("messages") or []):
        if _kind(message) == "human":
            content = getattr(message, "content", None)
            if content is None and isinstance(message, dict):
                content = message.get("content")
            if content:
                return str(content)
    return ""


def _thread_id(config: Any) -> str | None:
    try:
        return str((config or {}).get("configurable", {}).get("thread_id") or "") or None
    except AttributeError:
        return None
