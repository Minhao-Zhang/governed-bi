"""Shared ask_user interrupt / resume payloads (ADR 0014 thread state).

The interrupt value, the resume maps the UI already sends, and the row written
onto ``ServeState.clarifications`` after an authorised resume. Callers that
invent a second spelling of ``basis`` or ``resolution`` will desynchronise the
pending queue from the live prompt.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "CLARIFICATION_KIND",
    "BASIS_DATA_DEFINITION",
    "BASIS_RANKING_AMBIGUITY",
    "CLARIFICATION_BASES",
    "RESOLUTION_ANSWERED",
    "RESOLUTION_DECLINED",
    "RESOLUTION_DEFERRED",
    "DECLINE_CLOSED_TEXT",
    "interrupt_payload",
    "parse_basis",
    "parse_resume",
]

CLARIFICATION_KIND = "clarification"

BASIS_DATA_DEFINITION = "data_definition"
BASIS_RANKING_AMBIGUITY = "ranking_ambiguity"
CLARIFICATION_BASES: frozenset[str] = frozenset(
    {BASIS_DATA_DEFINITION, BASIS_RANKING_AMBIGUITY}
)

RESOLUTION_ANSWERED = "answered"
RESOLUTION_DECLINED = "declined"
RESOLUTION_DEFERRED = "deferred"

#: What the model is told when the reader declines or cancels a ranking question.
#: Fail-closed: do not invite another SQL guess.
DECLINE_CLOSED_TEXT = (
    "The user declined this clarification. Do not guess at a reading and do not "
    "call run_query. The turn ends here."
)


def parse_basis(raw: Any) -> str | None:
    """``basis`` if it is one of the two declared values, else ``None``."""
    value = str(raw or "").strip()
    return value if value in CLARIFICATION_BASES else None


def interrupt_payload(
    *,
    clarification_id: str,
    question: str,
    why: str,
    basis: str,
) -> dict[str, str]:
    """The value ``interrupt()`` surfaces. Extend, do not rename ``kind``."""
    return {
        "kind": CLARIFICATION_KIND,
        "clarification_id": clarification_id,
        "question": question,
        "why": why,
        "basis": basis,
    }


def parse_resume(resume: Any, *, why: str) -> tuple[str, str, bool]:
    """Human reply → ``(text, resolution, fail_closed)``.

    Resume maps the live UI already sends:

    * ``{answer}`` / ``{choice_id}`` / a bare string — proceed, ``answered``
    * ``{declined: true}`` — fail closed
    * ``{deferred: true}`` — proceed under ``why``; row stamped deferred
    * ``{cancelled: true}`` — ranking only, same as decline (fail closed)

    Definition "cancel" is UI-only and never reaches here: the client does not resume.
    """
    if isinstance(resume, Mapping):
        if resume.get("declined") or resume.get("cancelled"):
            return DECLINE_CLOSED_TEXT, RESOLUTION_DECLINED, True
        if resume.get("deferred"):
            constraint = why or "the stated reason"
            text = (
                "The user deferred this clarification. Proceed under this constraint: "
                f"{constraint}"
            )
            return text, RESOLUTION_DEFERRED, False
        for key in ("answer", "choice_id", "text"):
            value = resume.get(key)
            if value:
                return str(value), RESOLUTION_ANSWERED, False
        return "", RESOLUTION_ANSWERED, False
    return str(resume), RESOLUTION_ANSWERED, False
