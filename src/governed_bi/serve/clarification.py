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


def parse_resume(resume: Any, *, why: str, expected_id: str | None = None) -> tuple[str, str, bool]:
    """Human reply → ``(text, resolution, fail_closed)``.

    Resume maps the live UI already sends:

    * ``{answer}`` / ``{choice_id}`` / a bare non-empty string — proceed, ``answered``
    * ``{declined: true}`` — fail closed
    * ``{deferred: true}`` — proceed under ``why``; row stamped deferred
    * ``{cancelled: true}`` — ranking only, same as decline (fail closed)

    Definition "cancel" is UI-only and never reaches here: the client does not resume.

    **Anything else fails closed, and did not until 2026-09-18.** The unrecognised shapes
    fell through to ``("", "answered", False)`` and ``(str(resume), "answered", False)``, so:

    * ``{}`` told the model the analyst's answer was the empty string
    * ``None`` told it the answer was the literal ``"None"``
    * ``123`` and ``[1, 2, 3]`` became ``"123"`` and ``"[1, 2, 3]"``

    and each was stamped ``resolution: "answered"`` in the record, next to the real ones.
    The turn then proceeded to ``run_query`` on the strength of a reply nobody gave. The
    ``declined`` branch above was already fail-closed, so the two halves of this function
    disagreed about what an uninterpretable payload means; ``malformed`` is the same closure
    with its own name, because "the analyst declined" and "the transport sent something this
    cannot read" are different facts and the record has to keep them apart.

    ``expected_id`` is checked **only when the payload names one**. The live client sends
    ``{clarification_id, answer}`` (``ui/components/chat/clarification-prompt.tsx``), but the
    documented bare-string and flag shapes do not, and requiring an id would refuse the
    protocol's own vocabulary. A mismatch is a resume aimed at a question that is no longer
    the outstanding one — a stale tab, a replayed request — and answering the live question
    with it is how one clarification's answer lands on another.
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
