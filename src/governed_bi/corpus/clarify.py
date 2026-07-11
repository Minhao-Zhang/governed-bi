"""The human-answer write primitive for the clarification protocol (D12).

The curator records what it does not know as a :class:`Clarification` on an
asset's never-served **Audit** tier (see ``schemas.py``). A pluggable Responder
answers it in free text — a human SME in production, a Simulated SME in eval —
and this module ingests that answer.

``accept_answer`` is the single write primitive D12 adds to the engine; it
generalizes the once-planned ``certify()``. It takes an asset (or column) with an
**open** clarification and returns a copy in which the clarification is
``answered``, any Inference-tier ``edits`` are applied, and ``Provenance`` is
stamped as a human sign-off. The downstream round trip (``write_corpus`` +
``validate``) and the Responder itself stay out of the engine (D6 boundary).
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from .schemas import ClarificationStatus, ProvenanceSource, ProvenanceStatus

# Any asset/column carrying an ``audit`` field (Column, TableAsset, JoinAsset, ...).
NodeT = TypeVar("NodeT", bound=BaseModel)


def accept_answer(
    node: NodeT,
    *,
    by: str,
    answer: str,
    edits: dict[str, Any] | None = None,
    reason: str | None = None,
    at: str | None = None,
    status: ProvenanceStatus = ProvenanceStatus.certified,
) -> NodeT:
    """Accept a human/SME answer to a node's open clarification (D12).

    ``node`` is any asset or column carrying an ``audit`` field with an **open**
    clarification. Returns a deep copy — the input is never mutated — in which:

    - the clarification is flipped to ``answered`` (``answer``, ``answered_by``
      and ``at`` recorded);
    - each ``edits`` entry is applied to the node's own Inference-tier field
      (e.g. ``{"description": "customer id", "role": ColumnRole.key}``) and the
      result re-validated, so a bad value is caught;
    - ``Provenance`` is re-stamped ``source=human`` with the given ``status``
      (``certified`` by default, the D6 prod sign-off), recording ``by`` and, when
      given, ``reason`` / ``at`` on the ``extra="allow"`` provenance block while
      keeping the block's other fields.

    Raises:
        ValueError: if ``node`` has no audit tier, no clarification, or a
            clarification that is not ``open``; or if ``edits`` names a field the
            node does not have.
    """
    node_type = type(node)
    copy = node.model_copy(deep=True)

    audit = getattr(copy, "audit", None)
    if audit is None:
        raise ValueError(
            f"accept_answer: {node_type.__name__} has no audit tier; nothing to answer"
        )
    clar = audit.clarification
    if clar is None:
        raise ValueError(
            f"accept_answer: {node_type.__name__} audit carries no clarification"
        )
    if clar.status is not ClarificationStatus.open:
        raise ValueError(
            f"accept_answer: clarification is already {clar.status.value!r}, not 'open'"
        )

    # Flip the clarification to answered.
    clar.status = ClarificationStatus.answered
    clar.answer = answer
    clar.answered_by = by
    clar.at = at

    # Re-stamp provenance as a human sign-off, keeping the block's other fields.
    prov = audit.provenance
    prov.source = ProvenanceSource.human
    prov.status = status
    prov.by = by  # extra="allow"
    if reason is not None:
        prov.reason = reason
    if at is not None:
        prov.at = at

    # Apply Inference-tier edits, if any, re-validating so values are checked.
    if edits:
        unknown = set(edits) - set(node_type.model_fields)
        if unknown:
            raise ValueError(
                f"accept_answer: unknown edit field(s) for {node_type.__name__}: "
                f"{sorted(unknown)}"
            )
        data = copy.model_dump()
        data.update(edits)
        copy = node_type.model_validate(data)

    return copy
