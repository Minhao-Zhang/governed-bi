"""Curator loop step 3 — Adversary pass (D10).

An *independent* agent that tries to **refute** each proposed Inference/skill
asset before it commits. It re-derives or attacks the claim, runs falsifying
probe queries, and checks consistency + evidence. Verdict: accept / revise /
reject. Survivors flip ``proposed → draft``.

Why independent, not self-review: a model rarely refutes its own plausible
inference, and that is exactly where owner-less layers silently rot.

- **Dev (BIRD):** the adversary is the *only* reviewer (auto-accept on pass).
- **Prod (enterprise):** automated first-line reviewer before human certification (D6).

Both the proposer's claim/evidence and the adversary's verdict/reasons are
written into the asset's ``audit`` block → the Viz audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..corpus import Asset


class Verdict(str, Enum):
    accept = "accept"
    revise = "revise"
    reject = "reject"


@dataclass(frozen=True)
class AdversaryResult:
    verdict: Verdict
    reasons: str
    revised: "Asset | None" = None  # populated when verdict == revise


def refute(asset: "Asset") -> AdversaryResult:
    """Attempt to refute one proposed Inference/skill asset."""
    raise NotImplementedError("adversary pending; independent agent, refute-first prompt")
