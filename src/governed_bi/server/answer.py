"""Server step 10: answer assembly + reliability stamp (D5).

Best-effort tiering with a **reliability stamp** that has teeth (not just a
footer). Uncertainty aggregates into the stamp: a low-confidence join used, a
fenced-raw fallback, Corrective-RAG triggered, or a suspect column in scope all
lower the tier, which drives differential handling. High-stakes (leadership /
PII) escalates to human sign-off or SQL-only.

The tier logic here is deterministic and testable; the flow (``server.flow``)
feeds it the signals it accumulated while running the DAG.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# A join at or below this confidence lowers the stamp (tune on the eval). Kept
# here so the planner's raw confidence and the stamp threshold stay separate.
LOW_CONFIDENCE_JOIN = 0.7


class ReliabilityTier(str, Enum):
    governed = "governed"  # high stamp
    lineage = "lineage"  # medium stamp
    fenced_raw = "fenced_raw"  # low stamp
    refused = "refused"  # fail-closed


@dataclass(frozen=True)
class UncertaintySignals:
    """Flags that fired while answering; any of them lowers the stamp."""

    low_confidence_join: bool = False
    suspect_in_scope: bool = False
    fenced_raw_fallback: bool = False
    corrective_rag: bool = False
    repaired: bool = False  # the SQL only passed after one or more repair attempts

    def fired(self) -> list[str]:
        return [name for name, on in vars(self).items() if on]

    def any_fired(self) -> bool:
        return any(vars(self).values())


@dataclass(frozen=True)
class Answer:
    tier: ReliabilityTier
    text: str | None
    sql: str | None
    provenance: dict  # source tier + confidence + which uncertainty flags fired
    escalation: str | None = None  # populated on refuse (canned blob)


def reliability_tier(signals: UncertaintySignals) -> ReliabilityTier:
    """Map accumulated uncertainty to a tier. A clean run is ``governed``; any
    fired flag drops to ``lineage``; a fenced-raw fallback drops to ``fenced_raw``.
    """
    if signals.fenced_raw_fallback:
        return ReliabilityTier.fenced_raw
    if signals.any_fired():
        return ReliabilityTier.lineage
    return ReliabilityTier.governed


def assemble(
    *,
    text: str | None,
    sql: str | None,
    signals: UncertaintySignals,
    provenance: dict | None = None,
) -> Answer:
    """Build a non-refusal answer, stamping the tier from ``signals``."""
    prov = dict(provenance or {})
    prov["uncertainty_flags"] = signals.fired()
    return Answer(tier=reliability_tier(signals), text=text, sql=sql, provenance=prov)


def refusal(*, escalation: str, provenance: dict | None = None) -> Answer:
    """Build a fail-closed refusal (no text, no SQL executed)."""
    return Answer(
        tier=ReliabilityTier.refused,
        text=None,
        sql=None,
        provenance=dict(provenance or {}),
        escalation=escalation,
    )
