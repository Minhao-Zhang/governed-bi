"""Stage and outcome vocabulary for where a turn went and how it ended.

Text and pure functions only: no I/O, settings, or non-stdlib imports. Two axes:
:class:`Outcome` (turn ending) and :class:`Stage` (pipeline position).
Gradeability is orthogonal and does not live here. ADR 0005 and ADR 0006 stages
share one enum.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

__all__ = [
    "Stage",
    "Outcome",
    "FACET_STAGES",
    "TERMINAL_STAGES",
    "REFUSED_BY_TO_STAGE",
    "CRASH_REFUSED_BY",
    "ATTEMPT_CAP_REFUSED_BY",
    "INFRA_ERROR_PREFIX",
    "GRADER_ANSWER_PREFIXES",
    "classify_outcome",
    "classify_row",
]


class Stage(str, Enum):
    """A position in the pipeline, named as the live event stream names it.

    Declared-but-unemitted members are kept so instrumentation does not invent a
    second name. Absent keys in ``by_failed_stage`` mean not observed, not zero.
    """

    # ── Graph nodes, in execution order (ADR 0005 §3.1) ──
    #: Derives a turn from the conversation when the caller sends only a message.
    #: Optional: `START -> accept -> guard` on a server; `START -> guard` otherwise.
    accept = "accept"
    #: Rule-based input gate. ADR 0006 §6 owns the rules; this owns the name.
    guard = "guard"
    #: Follow-up rewritten into a self-contained question (multi-turn only).
    rewrite = "rewrite"
    #: Gate over ``NegativeExampleAsset``.
    negative_gate = "negative_gate"

    # ── The five facets, concurrent ──
    # Named individually: "which facet degraded" is a quotability input.
    facet_schema = "facet_schema"
    facet_term = "facet_term"
    facet_metric = "facet_metric"
    facet_entity = "facet_entity"
    facet_example = "facet_example"

    # ── Post-fan-in, deterministic ──
    #: Schema selection plus the second retrieval pass.
    route = "route"
    #: Reference closure. A total function of the hit set.
    resolve = "resolve"
    #: Steiner connectivity. A choice among paths, and therefore bounded.
    connect = "connect"
    #: Context rendering only.
    assemble = "assemble"

    # ── The agent loop ──
    # No `run_query` member: a passing query already emits `check` + `execute`.
    agent_core = "agent_core"
    #: Retrieve ``body`` of assets that rendered structure-only.
    read_body = "read_body"
    inspect_schema = "inspect_schema"
    sample_rows = "sample_rows"
    ask_user = "ask_user"

    # ── Execution governance (ADR 0006) ──
    #: The layer stack. A block here is a governance decision, never a crash.
    check = "check"
    #: A statement reached the database.
    execute = "execute"
    #: Re-executed after a passing recheck, delivered marked unverified.
    graded_delivery = "graded_delivery"
    #: Attempt cap terminated the turn. Distinct from crash and model refusal.
    cap = "cap"

    #: Turns the turn into a sentence. Adopts the agent's closing text when
    #: present; generates only when the loop ended without prose.
    narrate = "narrate"

    # ── Terminals ──
    refuse = "refuse"
    decline = "decline"
    #: Writes an ``Answer``. Every terminal path funnels here.
    stamp = "stamp"

    # ── Attributed after the fact by the offline analyser ──
    table_select = "table_select"
    sql_generate = "sql_generate"

    # ── Declared, not yet emitted ──
    repair = "repair"


#: Facet stages in fan-out order.
FACET_STAGES: tuple[Stage, ...] = (
    Stage.facet_schema,
    Stage.facet_term,
    Stage.facet_metric,
    Stage.facet_entity,
    Stage.facet_example,
)

#: Stages that end a turn. ``stamp`` is not one: every terminal passes through it.
TERMINAL_STAGES: frozenset[Stage] = frozenset({Stage.refuse, Stage.decline, Stage.cap})


class Outcome(str, Enum):
    """How a turn ended, from the measurement's point of view.

    ``crashed`` vs ``refused`` must stay separate: a crash is our bug; a refusal
    is the product working. ``capped`` is separate from both (ADR 0006 §5).
    Graded delivery is not an outcome — the turn ``answered``.
    """

    answered = "answered"
    refused = "refused"
    clarification = "clarification"
    capped = "capped"
    crashed = "crashed"


#: Where each ``refused_by`` value was decided. Inventory of legal values for
#: :func:`classify_outcome`.
REFUSED_BY_TO_STAGE: Mapping[str, Stage] = {
    "guard": Stage.guard,
    "negative_example": Stage.negative_gate,
    "no_schema_matched": Stage.route,
    "missing_join_path": Stage.connect,
    "over_connect_bounds": Stage.connect,
    "guardrail": Stage.check,
    "attempt_cap": Stage.cap,
    "model_error": Stage.agent_core,
}

#: ``refused_by`` values that mean our bug (crash wearing a refusal stamp).
CRASH_REFUSED_BY: frozenset[str] = frozenset({"model_error"})

#: Attempt-cap ``refused_by``. Named so rename stays in step with the table.
ATTEMPT_CAP_REFUSED_BY = "attempt_cap"

#: Grader could not finish — harness failure; turn is ``crashed``.
INFRA_ERROR_PREFIX = "infra_error:"

#: Grader error prefixes that describe an answer, not a crash.
GRADER_ANSWER_PREFIXES: frozenset[str] = frozenset({"gold_unusable:", "exec_error:"})


def classify_outcome(
    *,
    error: str | None,
    refused_by: str | None,
    has_sql: bool,
    clarification_requested: bool = False,
) -> Outcome:
    """Map terminal signals onto exactly one :class:`Outcome`.

    Precedence: infra error → crash refused_by → attempt cap → clarification →
    other refused_by → SQL present ⇒ answered, else crashed.
    """
    if error and error.startswith(INFRA_ERROR_PREFIX):
        return Outcome.crashed
    if refused_by in CRASH_REFUSED_BY:
        return Outcome.crashed
    if refused_by == ATTEMPT_CAP_REFUSED_BY:
        return Outcome.capped
    if clarification_requested:
        return Outcome.clarification
    if refused_by:
        return Outcome.refused
    if has_sql:
        return Outcome.answered
    return Outcome.crashed


def classify_row(row: Mapping[str, Any]) -> tuple[Outcome, Stage | None]:
    """Classify a recorded row, preferring a stamped ``outcome``.

    Returns ``(outcome, failed_stage)``. ``failed_stage`` is ``None`` when the
    turn did not fail.
    """
    stamped = row.get("outcome")
    if isinstance(stamped, str):
        try:
            outcome = Outcome(stamped)
        except ValueError:
            outcome = None
        if outcome is not None:
            failed = row.get("failed_stage")
            stage: Stage | None = None
            if isinstance(failed, str):
                try:
                    stage = Stage(failed)
                except ValueError:
                    stage = None
            return outcome, stage

    refused_by = row.get("refused_by")
    refused_by = refused_by if isinstance(refused_by, str) else None
    outcome = classify_outcome(
        error=row.get("error") if isinstance(row.get("error"), str) else None,
        refused_by=refused_by,
        has_sql=bool(row.get("generated_sql")),
        clarification_requested=bool(row.get("clarification_requested")),
    )
    stage = REFUSED_BY_TO_STAGE.get(refused_by) if refused_by else None
    return outcome, stage


def _assert_refusal_tables_are_closed() -> None:
    """Import-time: refusal tables closed; cap key maps to Stage.cap."""
    bad_stage = sorted(k for k, v in REFUSED_BY_TO_STAGE.items() if not isinstance(v, Stage))
    if bad_stage:  # pragma: no cover - import-time guard
        raise AssertionError(f"refused_by values mapping to a non-Stage: {bad_stage}")

    unmapped_crash = sorted(CRASH_REFUSED_BY - set(REFUSED_BY_TO_STAGE))
    if unmapped_crash:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"crash refusals with no stage: {unmapped_crash}."
        )

    if ATTEMPT_CAP_REFUSED_BY not in REFUSED_BY_TO_STAGE:  # pragma: no cover
        raise AssertionError(
            f"{ATTEMPT_CAP_REFUSED_BY!r} is not in REFUSED_BY_TO_STAGE, so "
            "classify_outcome would return `capped` for a value nothing else "
            "recognises."
        )
    if REFUSED_BY_TO_STAGE[ATTEMPT_CAP_REFUSED_BY] is not Stage.cap:  # pragma: no cover
        raise AssertionError(
            f"{ATTEMPT_CAP_REFUSED_BY!r} must map to Stage.cap, not "
            f"{REFUSED_BY_TO_STAGE[ATTEMPT_CAP_REFUSED_BY]!r}"
        )

    overlap = TERMINAL_STAGES & set(FACET_STAGES)
    if overlap:  # pragma: no cover - import-time guard
        raise AssertionError(f"a facet cannot be a terminal stage: {sorted(overlap)}")


_assert_refusal_tables_are_closed()
