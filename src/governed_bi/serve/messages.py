"""Readers over the ``messages`` channel. One implementation each, at the layer that owns it.

**This module exists because of an import order, and the order was right.**
:func:`last_ai_text` lived in ``api/trace_store.py``, which is where its first two callers were.
Its third caller is ``serve/nodes/narrate.py``, and ``tools/check_imports.py`` orders ``serve``
before ``api`` — so the node could not reach it. The options were to copy it into ``serve``,
which ``tools/check_one_implementation.py`` refuses and has already refused once for this exact
function, or to move it to the layer that actually owns the concept. The ``messages`` channel is
declared in ``serve/state.py``; a reader of it belongs beside the declaration, and ``api`` may
import ``serve``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["last_ai_text"]


def last_ai_text(state: Mapping[str, Any]) -> str | None:
    """The model's answer, via LangChain's own ``AIMessage.text``.

    Three callers that must not disagree: ``routes._shape``, which supplies it to a REST caller
    with no message channel to read; ``graph_app._record_node``, which supplies it to the audit
    log; and ``serve/nodes/narrate.py``, which adopts it as the turn's ``answer_text`` when the
    agent wrote one. Two readers of "what did the model say" is how the audit list, the response
    and the answer card drift apart.

    Not hand-flattened. The Responses API returns content as blocks (``[{"type": "text", ...},
    {"type": "reasoning", ...}]``), and an earlier draft walked them itself — which is
    re-implementing something ``langchain-core`` owns, and decision #1 records that v1's three
    layers over ``BaseChatModel`` were a mistake for exactly this reason. ``.text`` already
    concatenates the text blocks and ignores the rest.

    **A dict message is read too, and that is not defensive.** Restored from a checkpoint over
    the wire a message is a plain mapping with ``content`` as a list of blocks and no ``.text``
    property, so the ``getattr`` path alone returns ``None`` for every message of a rehydrated
    thread — which is a conversation that reloads with its answers missing.

    ``human`` and ``tool`` messages are skipped rather than filtered on ``type == "ai"``: a
    provider message type this code has not seen is more likely to be the answer than to be a
    turn of someone else's, and reading it is recoverable where skipping it is silent.
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
