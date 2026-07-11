"""Curator clarification loop (WS3, Increment 2 of the D12 build plan).

The curator, working a train question against a Facts-only asset it cannot
confidently describe, records what it does not know as a :class:`Clarification`
on the asset's never-served **Audit** tier, receives a free-text answer through
a :class:`Responder`, and ingests that answer via ``accept_answer`` (D12,
``docs/design-decisions.md``; ``docs/plans/clarification-sme-benchmark-build-plan.md``).

Per the D12 / 2026-07-08 engine-vs-product boundary, the engine owns the
**seam and the offline default only** — exactly like the existing
``Proposer`` / ``ChatClient`` protocols and their offline doubles
(``HeuristicProposer`` / ``StaticChatClient``). The real Responder
implementations (a human SME UI or CSV round-trip in production, a live-LLM
Simulated SME in eval), the LLM answer-to-edit parse step, and any git/PR
orchestration all stay **downstream** and are not built here. This module ships
only :class:`StaticResponder` and :func:`default_parse` so the whole loop runs
with no network and no LLM.

Two curator-facing entry points, both immutable (inputs are never mutated; every
asset comes back as a fresh deep copy, like :class:`HeuristicProposer`):

- :func:`emit_clarifications` — attach an open question to every gap it finds.
- :func:`resolve_clarifications` — answer the open questions through a Responder
  and fold each answer back in via ``accept_answer``.

This is a standalone module: it is deliberately **not** wired into the
deterministic ``curate()`` loop.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from ..corpus.clarify import accept_answer
from ..corpus.schemas import (
    Audit,
    Clarification,
    ClarificationStatus,
    Column,
    Provenance,
    ProvenanceSource,
    ProvenanceStatus,
    TableAsset,
)

# Below this the curator's Inference-tier guess is too weak to serve unasked: it
# earns a clarification (D12). A ``None`` confidence or a missing description is
# the same gap by another name.
_DEFAULT_CONFIDENCE_THRESHOLD = 0.75

# The parse step that turns a free-text answer into an Inference-tier edit dict.
# The real implementation (an LLM, or a data engineer) is injected downstream;
# :func:`default_parse` is the offline default.
Parse = Callable[[str, Any], dict[str, Any]]


@runtime_checkable
class Responder(Protocol):
    """The seam a human SME or a Simulated SME plugs into to answer a question.

    In production a Responder is a human SME (via a UI or a CSV round-trip); in
    eval it is a live-LLM Simulated SME briefed with domain meaning. Both stay
    **downstream** (D12 boundary); the engine ships only the offline
    :class:`StaticResponder` so the loop is runnable and testable without a
    network or a model.
    """

    def answer(self, question: str) -> str:
        """Return a free-text answer to a curator-emitted clarification question."""
        ...


class StaticResponder:
    """A scripted :class:`Responder` for offline runs and tests.

    Looks each question up in ``answers`` and falls back to ``default`` for an
    unknown question — the deterministic offline double, mirroring how
    ``StaticChatClient`` stands in for the live ``ChatClient``.
    """

    def __init__(self, answers: dict[str, str] | None = None, default: str = "") -> None:
        self._answers = dict(answers) if answers else {}
        self._default = default

    def answer(self, question: str) -> str:
        return self._answers.get(question, self._default)


def default_parse(answer: str, node: Any) -> dict[str, Any]:
    """The trivial offline parse: the free-text answer becomes the description.

    A real parse step (an LLM prompted with the ``node``'s Facts, or a data
    engineer) turns an answer into a richer structured edit and is injected
    downstream; this keeps the loop runnable offline. ``node`` is accepted for
    signature parity with that downstream parse and is unused here.
    """
    return {"description": answer}


# --------------------------------------------------------------------------- #
# Emit: attach open clarifications to gap nodes
# --------------------------------------------------------------------------- #


def emit_clarifications(
    tables: list[TableAsset],
    *,
    confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
    asked_by: str = "curator",
) -> list[TableAsset]:
    """Attach an open :class:`Clarification` to every gap the curator finds (D12).

    Returns fresh deep copies (inputs are never mutated, like
    :class:`HeuristicProposer`). A column or table is a **gap** needing a
    clarification when its ``description`` is ``None``, its ``confidence`` is
    ``None``, or its ``confidence`` is below ``confidence_threshold`` — i.e. the
    Inference-tier guess is missing or too weak to serve unasked.

    Idempotent: a node that already carries an ``audit.clarification`` is left
    untouched (the existing question, open or answered, is never overwritten).
    Any existing ``audit`` / ``provenance`` is preserved; when a gap node has no
    ``audit`` yet, a fresh ``curator`` / ``proposed`` stamp is created to hang the
    question on. Table-level and column-level gaps are handled in one pass.
    """
    return [
        _emit_table(t, confidence_threshold=confidence_threshold, asked_by=asked_by)
        for t in tables
    ]


def _emit_table(
    table: TableAsset, *, confidence_threshold: float, asked_by: str
) -> TableAsset:
    new_columns = [
        _emit_column(
            table, c, confidence_threshold=confidence_threshold, asked_by=asked_by
        )
        for c in table.columns
    ]
    update: dict[str, Any] = {"columns": new_columns}
    if _is_gap(table, confidence_threshold) and not _has_clarification(table):
        question = f"What does table `{table.physical_name}` represent?"
        update["audit"] = _with_clarification(table.audit, question, asked_by)
    return table.model_copy(update=update, deep=True)


def _emit_column(
    table: TableAsset, column: Column, *, confidence_threshold: float, asked_by: str
) -> Column:
    if not _is_gap(column, confidence_threshold) or _has_clarification(column):
        return column.model_copy(deep=True)
    question = (
        f"What does column `{table.physical_name}.{column.physical_name}` "
        f"represent, and is it reliable for analysis?"
    )
    return column.model_copy(
        update={"audit": _with_clarification(column.audit, question, asked_by)},
        deep=True,
    )


def _is_gap(node: Any, confidence_threshold: float) -> bool:
    """True when the node's Inference-tier guess is missing or too weak (D12)."""
    if node.description is None or node.confidence is None:
        return True
    return node.confidence < confidence_threshold


def _has_clarification(node: Any) -> bool:
    """True when the node already carries any clarification (keep emit idempotent)."""
    audit = node.audit
    return audit is not None and audit.clarification is not None


def _with_clarification(audit: Audit | None, question: str, asked_by: str) -> Audit:
    """Return an ``Audit`` carrying a fresh OPEN clarification.

    Preserves an existing ``audit`` (and its ``provenance``); when there is none,
    creates a ``curator`` / ``proposed`` stamp to attach the question to.
    """
    clarification = Clarification(question=question, asked_by=asked_by)
    if audit is None:
        return Audit(
            provenance=Provenance(
                source=ProvenanceSource.curator,
                status=ProvenanceStatus.proposed,
            ),
            clarification=clarification,
        )
    return audit.model_copy(update={"clarification": clarification}, deep=True)


# --------------------------------------------------------------------------- #
# Resolve: answer open clarifications through a Responder
# --------------------------------------------------------------------------- #


def resolve_clarifications(
    tables: list[TableAsset],
    responder: Responder,
    *,
    parse: Parse = default_parse,
    by: str = "sme",
    status: ProvenanceStatus = ProvenanceStatus.certified,
) -> list[TableAsset]:
    """Answer every open clarification through ``responder`` and fold it in (D12).

    For each table, first every **column** with an open clarification is resolved
    (ask the ``responder``, ``parse`` the answer into an Inference-tier edit, then
    ``accept_answer`` to write it), rebuilding the column list; then, if the
    **table** itself has an open clarification, it is resolved the same way while
    carrying the already-updated columns. Nodes without an open clarification pass
    through unchanged. ``accept_answer`` does the actual write (clarification
    flipped to ``answered``, edits applied, provenance stamped ``source=human``);
    provenance stamping is never reimplemented here. Returns fresh deep copies.
    """
    return [
        _resolve_table(t, responder=responder, parse=parse, by=by, status=status)
        for t in tables
    ]


def _resolve_table(
    table: TableAsset,
    *,
    responder: Responder,
    parse: Parse,
    by: str,
    status: ProvenanceStatus,
) -> TableAsset:
    new_columns = [
        _resolve_node(c, responder=responder, parse=parse, by=by, status=status)
        for c in table.columns
    ]
    # Carry the resolved columns before resolving the table's own clarification.
    table = table.model_copy(update={"columns": new_columns})
    return _resolve_node(table, responder=responder, parse=parse, by=by, status=status)


def _resolve_node(
    node: Any,
    *,
    responder: Responder,
    parse: Parse,
    by: str,
    status: ProvenanceStatus,
) -> Any:
    clar = _open_clarification(node)
    if clar is None:
        return node.model_copy(deep=True)  # unchanged pass-through, still a fresh copy
    answer = responder.answer(clar.question)
    edits = parse(answer, node)
    return accept_answer(node, by=by, answer=answer, edits=edits, status=status)


def _open_clarification(node: Any) -> Clarification | None:
    """The node's clarification iff it is still ``open``, else ``None``."""
    audit = getattr(node, "audit", None)
    if audit is None or audit.clarification is None:
        return None
    clar = audit.clarification
    return clar if clar.status is ClarificationStatus.open else None
