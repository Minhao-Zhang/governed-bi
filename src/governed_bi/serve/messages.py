"""Readers over the ``messages`` channel.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage

from governed_bi.registry.stages import Outcome

__all__ = ["last_ai_text", "last_proposed_sql", "surface_answer_text"]


def last_ai_text(state: Mapping[str, Any]) -> str | None:
    """Model answer via ``AIMessage.text``. A checkpoint dict has no ``.text``, so read it too.

    Do not re-flatten content blocks; ``langchain-core`` owns that. Skip ``human`` and ``tool``
    rather than requiring ``type == "ai"``: an unseen type is more likely the answer.
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


# Only these may show model prose. Other outcomes carry system copy, and backfilling model
# text over it makes a refusal read as an explanation.
_MAY_ADOPT_MODEL_TEXT: frozenset[str] = frozenset({Outcome.answered.value, Outcome.no_sql.value})


def surface_answer_text(answer: Mapping[str, Any], state: Mapping[str, Any]) -> str | None:
    """Prose for REST and audit. Never backfill model text over a governance terminal.

    ``no_sql`` is included: that turn's only content is the model's words, and if ``narrate``
    degraded the record would otherwise carry no answer text.
    """
    text = answer.get("answer_text")
    if text:
        return str(text)
    if answer.get("outcome") not in _MAY_ADOPT_MODEL_TEXT:
        return None
    return last_ai_text(state)


def _text_of(content: Any) -> str | None:
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
    """The last SQL proposed to ``run_query``, not a governed statement.

    The record's ``generated_sql`` is the statement the engine sent. One named reader, so
    the refusal fingerprint does not grow a second parser of the transcript.
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
