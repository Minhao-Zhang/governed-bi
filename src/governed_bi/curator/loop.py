"""Curator loop - propose, refute, promote (the deterministic core).

Orchestrates one per-DB pass over the Facts tier: run the proposer, let the
adversary refute the result, and on a clean pass promote every asset from
``proposed`` to ``draft`` (D10 lifecycle: ``proposed -> draft -> certified``).
Deterministic; no LLM and no network. :func:`curate` takes a :class:`Proposer`,
so swapping the deterministic :class:`~.proposer.HeuristicProposer` for an LLM
proposer changes nothing here.

This is the inner mechanism of the full loop described in ``docs/curator.md``.
The outer steps a live harness adds around it - profiling from a connector,
self-eval against train questions (measuring EX until it plateaus), and emitting
``corpus/<db>/`` (dev auto-accepts; prod opens a PR, D6) - layer on top; the
**done-enough** signal here is the machine-checkable half: ``CI green``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..corpus.schemas import ProvenanceStatus, TableAsset
from ..corpus.validate import Finding, is_green
from . import adversary

if TYPE_CHECKING:
    from ..gateway.connectors.base import Connector
    from .proposer import Proposer


@dataclass
class CurationResult:
    """Outcome of a :func:`curate` run.

    ``assets`` are the final table assets (promoted to ``draft`` when green);
    ``findings`` is the adversary's last verdict; ``rounds`` is how many
    propose/review passes ran; ``green`` is whether the adversary passed.
    """

    assets: list[TableAsset]
    findings: list[Finding]
    rounds: int
    green: bool


def _promote(tables: list[TableAsset]) -> list[TableAsset]:
    """Return copies with every ``proposed`` provenance flipped to ``draft``."""
    promoted: list[TableAsset] = []
    for table in tables:
        copy = table.model_copy(deep=True)
        _promote_provenance(copy)
        for col in copy.columns:
            _promote_provenance(col)
        promoted.append(copy)
    return promoted


def _promote_provenance(asset: TableAsset | object) -> None:
    """Flip an asset's provenance ``proposed -> draft`` in place, if present."""
    audit = getattr(asset, "audit", None)
    if audit is not None and audit.provenance.status is ProvenanceStatus.proposed:
        audit.provenance.status = ProvenanceStatus.draft


def curate(
    tables: list[TableAsset],
    proposer: "Proposer",
    *,
    connector: "Connector | None" = None,
    max_rounds: int = 3,
) -> CurationResult:
    """Run the propose -> refute -> promote loop over Facts-only ``tables``.

    Each round the ``proposer`` fills the Inference tier and the adversary
    (:func:`adversary.review`) refutes the result. On a green pass every asset's
    provenance is promoted ``proposed -> draft`` and the loop stops; otherwise it
    retries up to ``max_rounds``. Deterministic: a deterministic proposer that is
    not green on the first pass will not become green on a later one, so the cap
    is the honest termination guard (an LLM proposer would revise between rounds).
    """
    assets = tables
    findings: list[Finding] = []
    green = False
    rounds = 0

    for rounds in range(1, max_rounds + 1):
        assets = proposer.propose(assets)
        findings = adversary.review(assets, connector=connector)
        if is_green(findings):
            assets = _promote(assets)
            green = True
            break

    return CurationResult(assets=assets, findings=findings, rounds=rounds, green=green)
