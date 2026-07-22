"""Curator loop step 3 - Adversary pass (D10).

An *independent* reviewer that tries to **refute** each proposed Inference
asset before it commits. Two layers:

- :func:`review` -- the deterministic structural gate. It wraps the corpus CI
  validator (``corpus.validate.validate_corpus``: id conventions, duplicates,
  reference integrity, optional physical existence) and adds cheap heuristic
  self-consistency checks. Green (no findings) is the machine-checkable pass
  the loop needs; it runs with no LLM and no network.
- :func:`refute` -- the per-asset LLM seam. A model re-derives or attacks one
  claim, runs falsifying probe queries, and returns accept / revise / reject.
  Pending; this is where the ``deepagents`` adversary plugs in.

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

from ..corpus.schemas import ColumnRole, TableAsset
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

    Runs the corpus CI validator (reference integrity, id conventions, and, when
    a ``connector`` is given, physical existence) then layers cheap heuristic
    self-consistency checks on the proposer's Inference tier:

    - ``fk-missing-ref`` -- a column claiming ``role=foreign_key`` must name the
      column it references.
    - ``missing-provenance`` -- a curated table must carry an ``audit``
      provenance stamp; an asset asserted without provenance is unauditable.

    These are the deterministic floor. The richer, per-claim refutation (probe
    queries, evidence checks) is :func:`refute`, the LLM seam.
    """
    findings: list[Finding] = list(validate_corpus(assets, connector=connector))

    for asset in assets:
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
    """Attempt to refute one proposed Inference asset (LLM seam).

    The independent adversary re-derives or attacks the claim, runs falsifying
    probe queries, and checks consistency + evidence, returning a verdict. This
    is where the ``deepagents`` refute-first agent plugs in; the deterministic
    scaffold relies on :func:`review` instead.
    """
    raise NotImplementedError("per-asset refutation pending; independent LLM adversary")
