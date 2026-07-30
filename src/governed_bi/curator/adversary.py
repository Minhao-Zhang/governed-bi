"""Curator loop step 3 - Adversary pass (D10).

The adversary is **structural only**: it wraps the corpus CI validator
(``corpus.validate.validate_corpus``: id conventions, duplicates, reference
integrity, join-ON column membership, note budgets, optional physical
existence) and adds cheap heuristic self-consistency checks (:func:`review`).
Green (no findings) is the machine-checkable pass the loop needs; it runs with
no LLM and no network.

This checks *shape*, not *truth*: a corpus that clears :func:`review` is
structurally sound, not semantically certified. A per-asset LLM reviewer that
re-derives and falsifies each proposed claim was designed (see D10 in
[design-decisions.md](../../../docs/design-decisions.md)) but never reached a
caller — ``refute()`` sat behind ``NotImplementedError`` with zero call
sites and was deleted (2026-07-29) rather than left as an aspirational stub.

Hard vs soft findings: ``validate_corpus`` codes (dangling refs, bad ids,
missing physical tables, …) are **gating** — the pipeline must not write.
Heuristic codes in :data:`SOFT_ADVERSARY_CODES` are confidence penalties only.

- **Dev (BIRD):** the structural gate is the *only* automated reviewer.
- **Prod (enterprise):** automated first-line reviewer before human certification (D6).

Both the proposer's claim/evidence and the adversary's findings are written
into the asset's ``audit`` block -> the Viz audit trail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..corpus.schemas import ColumnRole, NoteAsset, TableAsset
from ..corpus.validate import Finding, validate_corpus

if TYPE_CHECKING:
    from ..corpus.schemas import Asset
    from ..gateway.connectors.base import Connector

# Heuristic self-consistency notes from :func:`review` — confidence penalties
# only. Everything else (including every ``validate_corpus`` code) is hard and
# blocks corpus write.
SOFT_ADVERSARY_CODES = frozenset({"missing-provenance", "fk-missing-ref"})


class StructuralGateError(RuntimeError):
    """Raised when hard structural findings must block corpus write (fail closed)."""

    def __init__(self, findings: list[Finding]):
        self.findings = list(findings)
        summary = "; ".join(str(f) for f in self.findings[:10])
        extra = "" if len(self.findings) <= 10 else f" (+{len(self.findings) - 10} more)"
        super().__init__(f"structural adversary blocked corpus write: {summary}{extra}")


def hard_findings(findings: list[Finding]) -> list[Finding]:
    """Return findings that must block write (everything except soft heuristics)."""
    return [f for f in findings if f.code not in SOFT_ADVERSARY_CODES]


def gate_hard_findings(findings: list[Finding]) -> None:
    """Raise :class:`StructuralGateError` when any hard finding remains."""
    hard = hard_findings(findings)
    if hard:
        raise StructuralGateError(hard)


def review(
    assets: list["Asset"],
    *,
    connector: "Connector | None" = None,
) -> list[Finding]:
    """Check a proposed corpus structurally. Returns findings; empty == pass.

    Runs the corpus CI validator then layers cheap heuristic self-consistency
    checks (FK refs, provenance stamps for tables and notes). Note C5 /
    publication-drift findings come from ``validate_corpus``.
    """
    findings: list[Finding] = list(validate_corpus(assets, connector=connector))

    for asset in assets:
        if isinstance(asset, NoteAsset):
            if asset.audit is None:
                findings.append(
                    Finding(
                        "missing-provenance",
                        asset.id,
                        "note asserted without an audit provenance stamp",
                    )
                )
            continue
        if not isinstance(asset, TableAsset):
            continue
        if asset.audit is None:
            findings.append(
                Finding(
                    "missing-provenance",
                    asset.id,
                    "table asserted without an audit provenance stamp",
                )
            )
        for col in asset.columns:
            if col.role is ColumnRole.foreign_key and col.references is None:
                findings.append(
                    Finding(
                        "fk-missing-ref",
                        asset.id,
                        f"column '{col.physical_name}' is a foreign_key but sets no references",
                    )
                )
    return findings
