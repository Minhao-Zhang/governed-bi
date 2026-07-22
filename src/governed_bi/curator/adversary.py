"""Curator loop step 3 - Adversary pass (D10).

An *independent* reviewer that tries to **refute** each proposed Inference
asset before it commits. Two layers:

- :func:`review` -- the deterministic structural gate. It wraps the corpus CI
  validator (``corpus.validate.validate_corpus``: id conventions, duplicates,
  reference integrity, optional physical existence) and adds cheap heuristic
  self-consistency checks. Green (no findings) is the machine-checkable pass
  the loop needs; it runs with no LLM and no network.
- :func:`refute` -- per-asset seam. Notes get an offline structural check;
  other assets still require the LLM adversary (model-gated).

Why an independent adversary, not self-review: a model rarely refutes its own
plausible inference, and that is exactly where owner-less layers silently rot.

- **Dev (BIRD):** the adversary is the *only* reviewer (auto-accept on pass).
- **Prod (enterprise):** automated first-line reviewer before human certification (D6).

Both the proposer's claim/evidence and the adversary's verdict/reasons are
written into the asset's ``audit`` block -> the Viz audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ..corpus.schemas import ColumnRole, NoteAsset, TableAsset
from ..corpus.validate import Finding, validate_corpus

if TYPE_CHECKING:
    from ..corpus.schemas import Asset
    from ..gateway.connectors.base import Connector


class Verdict(str, Enum):
    accept = "accept"
    revise = "revise"
    reject = "reject"


@dataclass(frozen=True)
class AdversaryResult:
    verdict: Verdict
    reasons: str
    revised: "Asset | None" = None  # populated when verdict == revise


def review(
    assets: list["Asset"],
    *,
    connector: "Connector | None" = None,
) -> list[Finding]:
    """Refute a proposed corpus structurally. Returns findings; empty == pass.

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


def refute(asset: "Asset") -> AdversaryResult:
    """Attempt to refute one proposed Inference asset.

    Offline path for notes: structural validate + empty-summary reject.
    Non-note assets still raise (LLM adversary is model-gated).
    """
    if isinstance(asset, NoteAsset):
        if not (asset.summary or "").strip():
            return AdversaryResult(Verdict.reject, "note.summary is empty")
        findings = validate_corpus([asset])
        if findings:
            return AdversaryResult(
                Verdict.revise, "; ".join(str(f) for f in findings)
            )
        return AdversaryResult(Verdict.accept, "structural note checks passed")
    raise NotImplementedError(
        "per-asset LLM refutation pending for non-note assets; use review() offline"
    )
