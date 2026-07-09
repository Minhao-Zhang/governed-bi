"""Server step 10: answer assembly + reliability stamp (D5).

The stamp reports on **two independent axes** (kept explicit so callers never
mistake one for the other):

- ``safety_clearance`` (bool): did a guardrail-passing query, executed as the
  requesting principal, back this answer? This is a *gate* - it is true for every
  assembled answer by construction (nothing reaches assembly until the five
  guardrail layers pass and execution succeeds) and false for every refusal. It
  says nothing about whether the number is right.
- ``semantic_assurance`` (enum): how well-grounded the answer is - ``certified``
  (clean run, no uncertainty flag), ``heuristic`` (a low-confidence join, suspect
  column in scope, Corrective-RAG, or a *repaired* query fired a flag),
  ``unverified`` (fenced-raw fallback), or ``none`` (refused). This is the axis
  that should drive automatic-delivery decisions and cache admission - an answer is
  never delivered/cached merely because it is *safe*.

``ReliabilityTier`` is the single-axis **projection** of these two that older
callers read (``governed`` == cleared + ``certified``, ``lineage`` == cleared +
``heuristic``, ``fenced_raw`` == ``unverified``, ``refused`` == not cleared). The
projection is kept 1:1 with ``semantic_assurance`` so the two never drift.

The thresholds and the signal set are **uncalibrated heuristics** - a first cut to
be tuned against the eval (which boundary catches the wrong answers without
over-refusing), not calibrated probabilities. The mapping here is deterministic
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


class SemanticAssurance(str, Enum):
    """How well-grounded the answer is - the epistemic axis, distinct from safety.

    Drives automatic-delivery and cache-admission decisions. ``certified`` is a
    clean run (NOT a verified-correct claim); any uncertainty flag drops it to
    ``heuristic``; a fenced-raw fallback is ``unverified``; a refusal is ``none``.
    Boundaries are uncalibrated heuristics tuned on the eval.
    """

    certified = "certified"  # clean run: no uncertainty flag fired
    heuristic = "heuristic"  # an uncertainty flag fired (low-conf join, suspect, repaired, ...)
    unverified = "unverified"  # fenced-raw fallback
    none = "none"  # refused: nothing delivered


class ReliabilityTier(str, Enum):
    """Single-axis **projection** of (safety_clearance, semantic_assurance).

    Kept for callers/UI that read one stamp; ``governed`` == cleared + certified,
    ``lineage`` == cleared + heuristic, ``fenced_raw`` == unverified, ``refused``
    == not cleared. Prefer the two explicit axes on :class:`Answer` for new logic.
    """

    governed = "governed"  # high stamp: safe + in-scope + no uncertainty flag fired
    lineage = "lineage"  # medium stamp: an uncertainty flag fired
    fenced_raw = "fenced_raw"  # low stamp: fenced-raw fallback
    refused = "refused"  # fail-closed


# The tier is the collapsed view of semantic_assurance (for a cleared answer) or
# ``refused`` (for one that never cleared). Single source of truth for the mapping.
_ASSURANCE_TO_TIER = {
    SemanticAssurance.certified: ReliabilityTier.governed,
    SemanticAssurance.heuristic: ReliabilityTier.lineage,
    SemanticAssurance.unverified: ReliabilityTier.fenced_raw,
    SemanticAssurance.none: ReliabilityTier.refused,
}


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
    tier: ReliabilityTier  # the single-axis projection (see ReliabilityTier)
    text: str | None
    sql: str | None
    provenance: dict  # source tier + confidence + which uncertainty flags fired
    escalation: str | None = None  # populated on refuse (canned blob)
    # The two explicit axes the tier collapses. Prefer these for new logic.
    safety_clearance: bool = False  # guardrail-passing + executed as principal
    semantic_assurance: SemanticAssurance = SemanticAssurance.none


def semantic_assurance(signals: UncertaintySignals) -> SemanticAssurance:
    """Map accumulated uncertainty to the epistemic axis. A clean run is
    ``certified`` (no flag fired, NOT verified correct); any fired flag drops to
    ``heuristic``; a fenced-raw fallback drops to ``unverified``. Uncalibrated
    heuristic, tuned against the eval.
    """
    if signals.fenced_raw_fallback:
        return SemanticAssurance.unverified
    if signals.any_fired():
        return SemanticAssurance.heuristic
    return SemanticAssurance.certified


def reliability_tier(signals: UncertaintySignals) -> ReliabilityTier:
    """The single-axis tier for a cleared answer: the projection of
    :func:`semantic_assurance`. Kept for callers/UI reading one stamp.
    """
    return _ASSURANCE_TO_TIER[semantic_assurance(signals)]


def assemble(
    *,
    text: str | None,
    sql: str | None,
    signals: UncertaintySignals,
    provenance: dict | None = None,
) -> Answer:
    """Build a non-refusal answer. Safety is cleared by construction (guardrails
    passed + executed); the semantic axis is derived from ``signals`` and the tier
    is its projection.
    """
    prov = dict(provenance or {})
    prov["uncertainty_flags"] = signals.fired()
    assurance = semantic_assurance(signals)
    return Answer(
        tier=_ASSURANCE_TO_TIER[assurance],
        text=text,
        sql=sql,
        provenance=prov,
        safety_clearance=True,
        semantic_assurance=assurance,
    )


def refusal(*, escalation: str, provenance: dict | None = None) -> Answer:
    """Build a fail-closed refusal (no text, no SQL executed): neither axis met."""
    return Answer(
        tier=ReliabilityTier.refused,
        text=None,
        sql=None,
        provenance=dict(provenance or {}),
        escalation=escalation,
        safety_clearance=False,
        semantic_assurance=SemanticAssurance.none,
    )
