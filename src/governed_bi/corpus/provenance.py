"""Phase-boundary re-stamp of model-authored governance and audit (ADR 0005 §1.5).

Exclusion and certified human provenance are human-only. A model that owns files
can mint them by writing YAML; the prompt telling it not to is not a control.
This function is.

**Restored 2026-08-07, on this branch only.** Upstream deleted this module and its ADR
paragraph (audit §10): it had zero callers there, so "built, never called" made it an
uncalled control rather than a real one. That premise does not hold on ``ryan/dev-v2``:
``corpus/drafts.py::submit_draft`` calls it as the phase-boundary guarantee behind the
whole draft-write foundation (UtkuAI, ported). Upstream's replacement control
(``tools/graft_corpus_fields.py`` refusing the whole ``governance``/``reliability``/
``summary`` fields) guards a different write path — the curator's model-authored-corpus
grafting tool, not this HTTP draft/approve flow — so the two are not redundant with each
other; keep both.
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
