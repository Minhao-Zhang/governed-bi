"""Gradeability does not live here. ADR 0005 and ADR 0006 stages share one enum."""

from __future__ import annotations

from enum import Enum
from typing import Mapping

__all__ = [
    "Stage",
    "Outcome",
    "CHANNEL_STAGES",
    "TERMINAL_STAGES",
    "REFUSED_BY_TO_STAGE",
    "ABSTENTION_REASONS",
    "CRASH_REFUSED_BY",
    "ATTEMPT_CAP_REFUSED_BY",
    "INFRA_ERROR_PREFIX",
    "GRADER_ANSWER_PREFIXES",
    "classify_outcome",
]


class Stage(str, Enum):
    """Declared-but-unemitted members stay, so instrumentation does not invent a second name.

    Absent keys in ``by_failed_stage`` mean not observed, not zero.
    """

    #: Optional. A server goes start, accept, guard; otherwise start, guard.
    accept = "accept"
    #: ADR 0006 owns the rules; this owns the name.
    guard = "guard"
    rewrite = "rewrite"

    # Named individually: which channel degraded is a quotability input.
    channel_schema = "channel_schema"
    channel_term = "channel_term"
    channel_metric = "channel_metric"
    channel_entity = "channel_entity"
    channel_example = "channel_example"

    route = "route"
    #: A total function of the hit set.
    resolve = "resolve"
    #: A choice among paths, and therefore bounded.
    connect = "connect"
    assemble = "assemble"

    # No `run_query` member: a passing query already emits `check` and `execute`.
    agent_core = "agent_core"
    read_body = "read_body"
    inspect_schema = "inspect_schema"
    sample_rows = "sample_rows"
    ask_user = "ask_user"

    #: A block here is a governance decision, never a crash.
    check = "check"
    execute = "execute"
    #: Delivered marked unverified.
    graded_delivery = "graded_delivery"
    #: Distinct from crash and model refusal.
    cap = "cap"

    #: Sits before the agent (ADR 0013). Deciding after five attempts is a report, not a decision.
    abstain = "abstain"

    #: Generates only when the loop ended without prose.
    narrate = "narrate"

    refuse = "refuse"
    decline = "decline"
    #: Every terminal path funnels here.
    finish = "finish"

    #: Attributed after the fact. Not a live stage.
    table_select = "table_select"
    sql_generate = "sql_generate"

    repair = "repair"


CHANNEL_STAGES: tuple[Stage, ...] = (
    Stage.channel_schema,
    Stage.channel_term,
    Stage.channel_metric,
    Stage.channel_entity,
    Stage.channel_example,
)

#: ``finish`` is not one: every terminal passes through it.
TERMINAL_STAGES: frozenset[Stage] = frozenset({Stage.refuse, Stage.decline, Stage.cap})


class Outcome(str, Enum):
    """``crashed``, ``refused``, and ``capped`` stay apart (ADR 0006).
    Graded delivery is ``answered``. ``no_sql`` names only that no governed
    statement ran. ``clarification`` counts a pause and a decline together.
    Whether the row was written is a field ``finish`` writes.
    """

    answered = "answered"
    refused = "refused"
    clarification = "clarification"
    capped = "capped"
    crashed = "crashed"
    #: Same spelling as the ledger ``terminal`` word; ``finish`` derives this member from that field.
    no_sql = "no_sql"


# Closed vocabulary of why the engine withheld, not which rule a layer refused under.
# Order is the policy's, in ``serve/abstention.py``. Why a vocabulary rather than a
# score, and the unattributed bucket: ADR 0013.
ABSTENTION_REASONS: frozenset[str] = frozenset({
    "retrieval_channel_failed",
    "nothing_retrieved",
    "empty_context",
    "retrieved_table_dropped",
})

REFUSED_BY_TO_STAGE: Mapping[str, Stage] = {
    "guard": Stage.guard,
    "no_schema_matched": Stage.route,
    # `resolve` has no stage of its own; its decline is attributed to connect.
    "over_resolve_bounds": Stage.connect,
    "missing_join_path": Stage.connect,
    "over_connect_bounds": Stage.connect,
    "guardrail": Stage.check,
    "guardrail_error": Stage.check,
    "attempt_cap": Stage.cap,
    "model_error": Stage.agent_core,
    **{reason: Stage.abstain for reason in sorted(ABSTENTION_REASONS)},
}

# Literals, not an import: this package sits below ``govern``, and ``layers.py`` asserts they agree.
# ``model_error`` has no producer in ``src/`` and stays declared.
# Why ``guardrail_error`` is a crash: ADR 0005.
CRASH_REFUSED_BY: frozenset[str] = frozenset({"model_error", "guardrail_error"})

ATTEMPT_CAP_REFUSED_BY = "attempt_cap"

#: Harness failure; the turn is ``crashed``.
INFRA_ERROR_PREFIX = "infra_error:"

#: These describe an answer, not a crash.
GRADER_ANSWER_PREFIXES: frozenset[str] = frozenset({"gold_unusable:", "exec_error:"})


def classify_outcome(
    *,
    error: str | None,
    refused_by: str | None,
    has_sql: bool,
    clarification_requested: bool = False,
    terminal: str | None = None,
) -> Outcome:
    """Precedence: infra error, crash ``refused_by``, cap, clarification,
    other refusal, SQL, then ``no_sql``, else crashed. ``terminal`` is read
    last, because a refusal also carries an empty ledger. A pause never calls
    this function. An absent ``terminal`` stays ``crashed``.
    """
    if error and error.startswith(INFRA_ERROR_PREFIX):
        return Outcome.crashed
    if refused_by in CRASH_REFUSED_BY:
        return Outcome.crashed
    if refused_by == ATTEMPT_CAP_REFUSED_BY:
        return Outcome.capped
    if clarification_requested:
        # A decline, not a pause: a pause raises GraphInterrupt and never arrives.
        # Before the general ``refused_by`` branch, so it outranks ``guardrail``.
        return Outcome.clarification
    if refused_by:
        return Outcome.refused
    if has_sql:
        return Outcome.answered
    if terminal == Outcome.no_sql.value:
        return Outcome.no_sql
    return Outcome.crashed


def _assert_refusal_tables_are_closed() -> None:
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

    overlap = TERMINAL_STAGES & set(CHANNEL_STAGES)
    if overlap:  # pragma: no cover - import-time guard
        raise AssertionError(f"a retrieval channel cannot be a terminal stage: {sorted(overlap)}")

    unmapped = sorted(ABSTENTION_REASONS - set(REFUSED_BY_TO_STAGE))
    if unmapped:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"abstention reasons with no stage: {unmapped}. A reason outside "
            "REFUSED_BY_TO_STAGE is a terminal nothing can attribute, which is the "
            "free-text refusal the closed vocabulary exists to replace."
        )
    misplaced = sorted(r for r in ABSTENTION_REASONS if REFUSED_BY_TO_STAGE[r] is not Stage.abstain)
    if misplaced:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"abstention reasons attributed to another stage: {misplaced}. A decision the "
            "abstention policy took must not be filed under the node it happens to be about, "
            "or the refusal histogram credits `route` with a policy's judgement."
        )
    crashing = sorted(ABSTENTION_REASONS & CRASH_REFUSED_BY)
    if crashing:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"abstention reasons classified as crashes: {crashing}. Declining on purpose is "
            "the product working; `Outcome` requires it stay apart from our own bugs."
        )

    # Every declared refusal outranks an empty ledger: a blocked turn also has
    # terminal "no_sql", and only Outcome.no_sql reads that field. Checked over the
    # vocabulary, because a silent miss would drop the reason from the histogram.
    leaked = sorted(
        reason
        for reason in REFUSED_BY_TO_STAGE
        if reason not in CRASH_REFUSED_BY
        and reason != ATTEMPT_CAP_REFUSED_BY
        and classify_outcome(
            error=None, refused_by=reason, has_sql=False, terminal=Outcome.no_sql.value
        ) is not Outcome.refused
    )
    if leaked:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"declared refusals reclassified once the ledger was consulted: {leaked}. "
            "`Outcome.no_sql` means nothing decided and no statement ran; a turn something "
            "refused is a decision, and it carries an empty ledger too."
        )


_assert_refusal_tables_are_closed()
