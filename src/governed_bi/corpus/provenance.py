"""Phase-boundary re-stamp of model-authored governance and audit (ADR 0005 §1.5).

Exclusion and certified human provenance are human-only; this call strips forgeries.
"""


from __future__ import annotations

from dataclasses import replace
from typing import TypeVar

from .schema import Asset, Audit, Governance, Provenance, ProvenanceSource, ProvenanceStatus

__all__ = ["restamp_model_authored"]

A = TypeVar("A", bound=Asset)


def restamp_model_authored(asset: A, *, model: str | None = None) -> A:
    """Strip forged ``governance`` / certified human ``audit``; stamp model provenance.

    ``governance.excluded`` and human-certified audit cannot survive this call.
    Reliability (including ``suspect``) is AI-authorable and is left alone.
    """
    audit = Audit(
        provenance=Provenance(
            source=ProvenanceSource.curator,
            status=ProvenanceStatus.proposed,
            model=model,
        ),
        evidence=asset.audit.evidence if asset.audit is not None else None,
    )
    return replace(asset, governance=Governance(), audit=audit)
