"""One spelling of the ``ask_user`` interrupt and resume payloads.

A second spelling of ``basis`` or ``resolution`` desynchronises the pending queue from the
live prompt.
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
    "RESOLUTION_MALFORMED",
    "DECLINE_CLOSED_TEXT",
    "MALFORMED_CLOSED_TEXT",
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

#: The payload could not be read as a reply at all. **Not** ``declined``: nobody declined
#: anything, and a record that spelled the two the same would report a reader's decision
#: where there was a transport fault, and vice versa. See :func:`parse_resume`.
RESOLUTION_MALFORMED = "malformed"

#: What the model is told when the reader declines or cancels a ranking question.
#: Fail-closed: do not invite another SQL guess.
DECLINE_CLOSED_TEXT = (
    "The user declined this clarification. Do not guess at a reading and do not "
    "call run_query. The turn ends here."
)

#: The same closure for an unreadable payload, worded so the model does not report a
#: refusal the analyst never made.
MALFORMED_CLOSED_TEXT = (
    "No readable answer arrived for this clarification. Do not guess at a reading and do "
    "not call run_query. The turn ends here."
)


def parse_basis(raw: Any) -> str | None:
    value = str(raw or "").strip()
    return value if value in CLARIFICATION_BASES else None


def interrupt_payload(
    *,
    clarification_id: str,
    question: str,
    why: str,
    basis: str,
    choices: list[str] | None = None,
) -> dict[str, Any]:
    """The value ``interrupt()`` surfaces. Extend, do not rename ``kind``."""
    payload: dict[str, Any] = {
        "kind": CLARIFICATION_KIND,
        "clarification_id": clarification_id,
        "question": question,
        "why": why,
        "basis": basis,
    }
    # Absent when the question is freeform, so an older client sees the keys it knows.
    if choices:
        payload["choices"] = list(choices)
    return payload


def parse_resume(resume: Any, *, why: str, expected_id: str | None = None) -> tuple[str, str, bool]:
    """Human reply to ``(text, resolution, fail_closed)``. Unreadable payloads fail closed.

    ``malformed`` is not ``declined``: a transport fault is not a reader's decision.
    Check ``expected_id`` only when named; a mismatch would answer the live question.
    """
    named = resume.get("clarification_id") if isinstance(resume, Mapping) else None
    if expected_id is not None and named is not None and str(named) != expected_id:
        return (
            "This resume names a different clarification than the one outstanding, so it was "
            "not applied. The question is still unanswered.",
            RESOLUTION_MALFORMED,
            True,
        )

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
        return MALFORMED_CLOSED_TEXT, RESOLUTION_MALFORMED, True
    if isinstance(resume, str) and resume.strip():
        return resume, RESOLUTION_ANSWERED, False
    return MALFORMED_CLOSED_TEXT, RESOLUTION_MALFORMED, True
