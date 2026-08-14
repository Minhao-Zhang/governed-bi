"""The served graph's entry node, bound to the session that mints its turns (ADR 0007 §2).

A client submits one key — ``{messages: [{type: "human", content}]}`` — and the record requires
fifteen fields. This node derives them from the last human message through
:meth:`~governed_bi.serve.session.Session.turn`, so nothing a client sends in a provenance field
is merged: ``run_id``, ``turn_id``, ``corpus_content_hash``, ``prompt_set_hash`` and
``knobs_resolved`` are the run's own claims about itself, and every quotability gate reads them.

**Here rather than in ``api/graph_app.py``, and taking its session as an argument.** It lived
there as a closure over a module-level ``_SESSION``, which is why no test ever executed it: the
only way to reach the node was to build a Postgres-backed session from the environment. It is
``serve/`` code — it writes ``ServeState`` and reads nothing from the HTTP layer — and
``tools/check_imports.py`` orders ``serve`` before ``api``, so the dependency ran the wrong way
as well.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from governed_bi.serve.state import PER_TURN_RESET

__all__ = ["accept_node"]


def accept_node(session: Any) -> Callable[[dict, Any], dict]:
    """The ``accept`` node for ``session``.

    Returned rather than declared, because the session is a live object graph — policy,
    connector, index, models — and LangGraph Server can only put JSON on
    ``config["configurable"]``. Closing over it here is the same move ADR 0007 §1 makes for the
    graph factory, one level down.
    """

    def accept(state: dict, config: Any) -> dict:
        """Derive a turn from the conversation. Client provenance fields are ignored."""
        question = _last_human(state)
        if not question:
            # Clear prior-turn LastValue channels before jumping to stamp. Without this (and
            # without the accept→stamp edge) guard would still run on the previous question.
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
        turn = session.turn(question, turn_index=max(1, prior), thread_id=_thread_id(config))
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
