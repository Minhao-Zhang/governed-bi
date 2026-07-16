"""Analyst step 10: answer assembly + reliability stamp (D5).

The stamp reports on **two independent axes** (kept explicit so callers never
mistake one for the other):

- ``safety_clearance`` (bool): did a guardrail-passing query, executed as the
  requesting principal, back this answer? This is a *gate* - it is true for every
  assembled answer by construction (nothing reaches assembly until the five
  guardrail layers pass and execution succeeds) and false for every refusal. It
  says nothing about whether the number is right.
- ``semantic_assurance`` (enum): how well-grounded the answer is - ``grounded``
  (clean run, no uncertainty flag), ``heuristic`` (a low-confidence join, suspect
  column in scope, Corrective-RAG, or a *repaired* query fired a flag),
  ``unverified`` (fenced-raw fallback), or ``none`` (refused). This is the axis
  that should drive automatic-delivery decisions and cache admission - an answer is
  never delivered/cached merely because it is *safe*.

The two-axis stamp above is the **canonical** reliability vocabulary.
``ReliabilityTier`` is a **display-only** single-axis projection of it, kept only
for a compact UI badge and the fail-closed refusal check, never a second
vocabulary (``governed`` == cleared + ``grounded``, ``lineage`` == cleared +
``heuristic``, ``fenced_raw`` == ``unverified``, ``refused`` == not cleared). The
projection is kept 1:1 with ``semantic_assurance`` so the two never drift.

The thresholds and the signal set are **uncalibrated heuristics** - a first cut to
be tuned against the eval (which boundary catches the wrong answers without
over-refusing), not calibrated probabilities. The mapping here is deterministic
and testable; the agent core (``analyst.agent``) feeds it the signals it
accumulated while running the outer ``StateGraph`` + inner agent loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# A join at or below this confidence lowers the stamp. Uncalibrated/tunable
# threshold (a guessed cut, tune on the eval), not a calibrated probability. Kept
# here so the planner's raw confidence and the stamp threshold stay separate.
LOW_CONFIDENCE_JOIN = 0.7


class SemanticAssurance(str, Enum):
    """How well-grounded the answer is - the epistemic axis, distinct from safety.

    Drives automatic-delivery and cache-admission decisions. ``grounded`` is a
    clean run (NOT a verified-correct claim); any uncertainty flag drops it to
    ``heuristic``; a fenced-raw fallback is ``unverified``; a refusal is ``none``.
    Boundaries are uncalibrated heuristics tuned on the eval.
    """

    grounded = "grounded"  # clean run: no uncertainty flag fired
    heuristic = "heuristic"  # an uncertainty flag fired (low-conf join, suspect, repaired, ...)
    unverified = "unverified"  # fenced-raw fallback
    none = "none"  # refused: nothing delivered


class ReliabilityTier(str, Enum):
    """Display-only single-axis projection of (safety_clearance, semantic_assurance).

    NOT a canonical reliability vocabulary - the two-axis stamp on :class:`Answer`
    is canonical; this exists only so a compact UI badge or the fail-closed
    refusal check has one value to read. ``governed`` == cleared + ``grounded``,
    ``lineage`` == cleared + ``heuristic``, ``fenced_raw`` == ``unverified``,
    ``refused`` == not cleared. New logic should read the two explicit axes on
    :class:`Answer`, not this tier.
    """

    governed = "governed"  # display label: safe + in-scope + no uncertainty flag fired
    lineage = "lineage"  # display label: an uncertainty flag fired
    fenced_raw = "fenced_raw"  # display label: fenced-raw fallback
    refused = "refused"  # fail-closed (also used structurally to detect a refusal)


# Display-only collapse of semantic_assurance (for a cleared answer) into one
# tier label, or ``refused`` (for one that never cleared). Single source of truth
# for the mapping so the projection and the two axes never drift; this table does
# not make the tier a second reliability vocabulary.
_ASSURANCE_TO_TIER = {
    SemanticAssurance.grounded: ReliabilityTier.governed,
    SemanticAssurance.heuristic: ReliabilityTier.lineage,
    SemanticAssurance.unverified: ReliabilityTier.fenced_raw,
    SemanticAssurance.none: ReliabilityTier.refused,
}


# Cap on rows carried in an Answer for display/audit. The gateway already caps
# the executed result; this bounds what a caller/UI renders (and keeps the Answer
# light). Tunable.
RESULT_PREVIEW_ROWS = 50


@dataclass(frozen=True)
class ResultTable:
    """A bounded snapshot of the executed result grid, for display and audit.

    Carries the actual column names and rows the query returned (clipped to
    ``RESULT_PREVIEW_ROWS``) so a caller/UI can show the data itself, not just the
    ``row_count`` shape. ``row_count`` is the full executed count; ``truncated``
    is True if the gateway cap or the preview cap clipped the rows carried here.
    """

    columns: list[str]
    rows: list[tuple]
    row_count: int
    truncated: bool = False


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
    tier: ReliabilityTier  # display-only single-axis projection (see ReliabilityTier)
    text: str | None
    sql: str | None
    provenance: dict  # source tier + confidence + which uncertainty flags fired
    escalation: str | None = None  # populated on refuse (canned blob)
    # The two canonical axes; the tier above is a display-only collapse of these.
    safety_clearance: bool = False  # guardrail-passing + executed as principal
    semantic_assurance: SemanticAssurance = SemanticAssurance.none
    result: "ResultTable | None" = None  # the executed rows (None on refusal)


def semantic_assurance(signals: UncertaintySignals) -> SemanticAssurance:
    """Map accumulated uncertainty to the epistemic axis. A clean run is
    ``grounded`` (no flag fired, NOT verified correct); any fired flag drops to
    ``heuristic``; a fenced-raw fallback drops to ``unverified``. Uncalibrated
    heuristic, tuned against the eval.
    """
    if signals.fenced_raw_fallback:
        return SemanticAssurance.unverified
    if signals.any_fired():
        return SemanticAssurance.heuristic
    return SemanticAssurance.grounded


def reliability_tier(signals: UncertaintySignals) -> ReliabilityTier:
    """The display-only tier for a cleared answer: the projection of
    :func:`semantic_assurance`. Kept for callers/UI reading one compact stamp,
    never a second reliability vocabulary.
    """
    return _ASSURANCE_TO_TIER[semantic_assurance(signals)]


def assemble(
    *,
    text: str | None,
    sql: str | None,
    signals: UncertaintySignals,
    provenance: dict | None = None,
    result: "ResultTable | None" = None,
) -> Answer:
    """Build a non-refusal answer. Safety is cleared by construction (guardrails
    passed + executed); the semantic axis is derived from ``signals`` and the tier
    is its projection. ``result`` carries the executed rows for display/audit.
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
        result=result,
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


def graded_delivery(
    *,
    sql: str,
    provenance: dict | None = None,
    result: "ResultTable | None" = None,
    text: str | None = None,
) -> Answer:
    """pipeline-design §6: deliver SQL with ``unverified`` assurance instead of refusing.

    Used when a *semantic* failure (coverage / L3–L5 repair exhaustion / execution
    exhaustion) would formerly hard-refuse, but a generated SQL exists to grade.
    Safety failures (L2, curated refuse-gate) must not call this.
    """
    prov = dict(provenance or {})
    prov["graded_delivery"] = True
    prov.setdefault("uncertainty_flags", [])
    if "fenced_raw_fallback" not in prov["uncertainty_flags"]:
        prov["uncertainty_flags"] = list(prov["uncertainty_flags"]) + ["fenced_raw_fallback"]
    return Answer(
        tier=ReliabilityTier.fenced_raw,
        text=text,
        sql=sql,
        provenance=prov,
        escalation=None,
        safety_clearance=False,  # did not clear the full guardrail path
        semantic_assurance=SemanticAssurance.unverified,
        result=result,
    )
