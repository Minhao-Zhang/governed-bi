"""Serve-time clarification (HITL) — the ``ask_user`` payload contract.

Implements the server side of
``docs/plans/hitl-clarification-contract.md``. The agent asks the user one
question mid-turn via the ``ask_user`` tool, which calls ``interrupt(request)``;
the request surfaces to the ``useStream`` client as ``stream.interrupt.value``.
The client answers with ``stream.respond(response)``; that ``response`` is what
``interrupt`` returns.

These are plain JSON-able dicts (no pydantic) because they cross the LangGraph
interrupt boundary and are re-serialized by the server; the shapes here are the
single source of truth the frontend mirrors (contract §3/§4/§9).
"""

from __future__ import annotations

import hashlib
from typing import Any


def new_clarification_id(question: str, *, salt: str = "") -> str:
    """A stable id for one clarification (join key across interrupt / resume /
    ledger / provenance). Deterministic in ``(question, salt)`` so a re-run of the
    same turn re-derives the same id (no ``Math.random`` / clock)."""
    digest = hashlib.sha1(f"{salt}\x00{question}".encode()).hexdigest()[:8]
    return f"clar_{digest}"


def clarification_request(
    question: str,
    why: str,
    *,
    clarification_id: str | None = None,
    choices: list[dict[str, str]] | None = None,
    allow_freeform: bool = True,
    salt: str = "",
) -> dict[str, Any]:
    """Build a ``ClarificationRequest`` (contract §3) — the value passed to
    ``interrupt``."""
    req: dict[str, Any] = {
        "kind": "clarification",
        "clarification_id": clarification_id or new_clarification_id(question, salt=salt),
        "question": question,
        "why": why,
        "tier": "audit",
    }
    if choices:
        req["choices"] = choices
        req["allow_freeform"] = allow_freeform
    return req


def parse_response(response: Any) -> dict[str, Any]:
    """Normalize a ``ClarificationResponse`` (contract §4) coming back from
    ``interrupt``/``stream.respond``.

    Returns ``{"declined": bool, "answer": str, "clarification_id": str|None}``.
    Tolerant: a bare string (some clients call ``respond("text")``) is treated as
    a freeform answer; an empty answer is treated as a decline.
    """
    if isinstance(response, str):
        text = response.strip()
        return {"declined": not text, "answer": text, "clarification_id": None}
    if not isinstance(response, dict):
        return {"declined": True, "answer": "", "clarification_id": None}
    cid = response.get("clarification_id")
    if response.get("declined") is True:
        return {"declined": True, "answer": "", "clarification_id": cid}
    if "choice_id" in response and response["choice_id"]:
        return {"declined": False, "answer": str(response["choice_id"]), "clarification_id": cid}
    answer = str(response.get("answer") or "").strip()
    return {"declined": not answer, "answer": answer, "clarification_id": cid}
