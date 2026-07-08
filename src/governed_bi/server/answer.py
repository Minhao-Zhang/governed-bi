"""Server step 10: answer assembly + reliability stamp (D5).

The stamp is a **governance + uncertainty stamp, not a correctness guarantee**.
``governed`` means only "safe + in-scope + no uncertainty flag fired" (the SQL
cleared the five guardrail layers and nothing in ``UncertaintySignals`` tripped);
it does NOT mean the answer was verified correct against ground truth. Uncertainty
aggregates into the stamp: a low-confidence join used, a fenced-raw fallback,
Corrective-RAG triggered, a suspect column in scope, or a repaired query all lower
the tier, which drives differential handling. High-stakes (leadership / PII)
escalates to human sign-off or SQL-only.

The thresholds and the signal set are **uncalibrated heuristics** - a first cut to
be tuned against the eval (which tier boundary catches the wrong answers without
over-refusing), not calibrated probabilities. The tier logic here is deterministic
and testable; the flow (``server.flow``) feeds it the signals it accumulated while
running the DAG.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# A join at or below this confidence lowers the stamp. Uncalibrated/tunable
# threshold (a guessed cut, tune on the eval), not a calibrated probability. Kept
# here so the planner's raw confidence and the stamp threshold stay separate.
LOW_CONFIDENCE_JOIN = 0.7


class ReliabilityTier(str, Enum):
    """A governance + uncertainty stamp, NOT a correctness guarantee.

    The tier reports how the answer was produced (safe + in-scope, and which
    uncertainty flags fired), not whether the number is right. ``governed`` is the
    clean-run stamp, not a verified-correct claim; the boundaries are uncalibrated
    heuristics tuned on the eval.
    """

    governed = "governed"  # high stamp: safe + in-scope + no uncertainty flag fired
    lineage = "lineage"  # medium stamp: an uncertainty flag fired
    fenced_raw = "fenced_raw"  # low stamp: fenced-raw fallback
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
    """Map accumulated uncertainty to a tier. A clean run is ``governed`` (safe +
    in-scope + no flag fired, NOT verified correct); any fired flag drops to
    ``lineage``; a fenced-raw fallback drops to ``fenced_raw``. The mapping is an
    uncalibrated heuristic to be tuned against the eval.
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
