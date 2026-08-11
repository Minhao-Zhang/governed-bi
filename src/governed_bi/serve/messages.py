"""Readers over the ``messages`` channel. One implementation each, at the layer that owns it.

:func:`last_ai_text` lived in ``api/trace_store.py`` where its first two callers were, but
``tools/check_imports.py`` orders ``serve`` before ``api``, so its third caller
(``serve/nodes/narrate.py``) could not reach it. The ``messages`` channel is declared in
``serve/state.py``; a reader of it belongs beside the declaration, and ``api`` may import
``serve``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage

__all__ = ["last_ai_text", "last_proposed_sql", "surface_answer_text"]


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

def last_proposed_sql(messages: Sequence[Any]) -> str | None:
    """The last SQL the model *proposed* to ``run_query``, from its tool-call arguments.

    **This is not a governed statement.** It is what the model asked for, which may have been
    refused by the layer stack, blocked by the attempt cap, or never have reached ``prepare()`` at
    all. The record's ``generated_sql`` deliberately does not carry it (audit C4): that field is
    declared as "the statement the engine SENT" and two callers execute it.

    It lives here, public and named for what it is, because exactly one consumer wants it —
    ``eval/harness._abstained_fingerprint``, which prices what a refusal cost by running the
    proposal read-only. Extracting it there from the transcript would be a second implementation
    of "which tool call was the last run_query", and this repository has paid for that shape.
    """
    last: str | None = None
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or ():
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
            if name != "run_query":
                continue
            args = call.get("args") if isinstance(call, dict) else getattr(call, "args", {})
            sql = (args or {}).get("sql")
            if sql:
                last = str(sql)
    return last
