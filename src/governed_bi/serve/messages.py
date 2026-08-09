"""Readers over the ``messages`` channel. One implementation each, at the layer that owns it.

:func:`last_ai_text` lived in ``api/trace_store.py`` where its first two callers were, but
``tools/check_imports.py`` orders ``serve`` before ``api``, so its third caller
(``serve/nodes/narrate.py``) could not reach it. The ``messages`` channel is declared in
``serve/state.py``; a reader of it belongs beside the declaration, and ``api`` may import
``serve``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["last_ai_text", "surface_answer_text"]


def last_ai_text(state: Mapping[str, Any]) -> str | None:
    """The model's answer, via LangChain's own ``AIMessage.text``.

    Three callers that must not disagree — ``routes._shape``, ``graph_app._record_node`` and
    ``serve/nodes/narrate.py`` — because two readers of "what did the model say" is how the
    audit list, the response and the answer card drift apart.

    Not hand-flattened: the Responses API returns content as blocks and ``.text`` already
    concatenates the text ones (decision #1 — do not re-implement what ``langchain-core`` owns).
    A dict message is read too, and that is not defensive: restored from a checkpoint over the
    wire a message is a plain mapping with no ``.text``, so the ``getattr`` path alone returns
    ``None`` for every message of a rehydrated thread.

    ``human`` and ``tool`` are skipped rather than filtering on ``type == "ai"``: an unseen
    provider message type is more likely to be the answer than someone else's turn, and reading
    it is recoverable where skipping it is silent.
    """
    for message in reversed(state.get("messages") or []):
        kind = str(getattr(message, "type", "") or "")
        if not kind and isinstance(message, Mapping):
            kind = str(message.get("type") or "")
        if kind in ("human", "tool"):
            continue
        text = getattr(message, "text", None)
        if not text and isinstance(message, Mapping):
            text = _text_of(message.get("content"))
        if text:
            return str(text)
    return None


def surface_answer_text(answer: Mapping[str, Any], state: Mapping[str, Any]) -> str | None:
    """Prose for REST/audit. Never backfill model text over a governance terminal.

    ``narrate`` already skips refuse/decline/crashed and ledger capped/refused. The old
    ``answer_text or last_ai_text(...)`` at the boundary undid that and put model prose on
    crashed/capped/refused turns. Only ``outcome=answered`` may adopt leftover AI text.
    """
    text = answer.get("answer_text")
    if text:
        return str(text)
    if answer.get("outcome") != "answered":
        return None
    return last_ai_text(state)


def _text_of(content: Any) -> str | None:
    """The text of a raw ``content`` value — a string, or the text blocks of a block list."""
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "text"
        ]
        joined = "".join(parts).strip()
        return joined or None
    return None
