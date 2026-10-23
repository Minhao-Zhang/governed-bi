"""Sole writer of ``answer``. See ADR 0005."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from governed_bi.govern.guard import GUARD_PUBLIC_MESSAGE
from governed_bi.govern.layers import GUARDRAIL_ERROR, GUARDRAIL_REFUSED_BY
from governed_bi.measure.degradation import channels_degraded
from governed_bi.registry.quantity import Measured
from governed_bi.registry.record import project
from governed_bi.registry.stages import ATTEMPT_CAP_REFUSED_BY, Outcome, Stage, classify_outcome
from governed_bi.serve.agent_state import NO_ANSWER_CALL
from governed_bi.serve.events import emit, rail_event_id
from governed_bi.serve.ledger import answering_attempts, attempt_field
from governed_bi.serve.outcome import OutcomeInputs, TurnOutcome, normalised

__all__ = ["classify_turn", "finish"]


def _usage_for_turn(state: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The channel keeps every turn."""
    turn_index = state.get("turn_index", 1)
    raw = state.get("usage") or []
    return [u for u in raw if isinstance(u, Mapping) and u.get("turn_index") == turn_index]


def _cache_total(usage: list[Mapping[str, Any]], field: str) -> int | Measured[int]:
    """An explicit ``0`` is a measurement. Silence is not a zero."""
    total = 0
    seen = False
    for row in usage:
        value = row.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        seen = True
        total += value
    if not seen:
        return Measured.unmeasured(
            f"no model call this turn reported {field}; the provider was not asked and did not say"
        )
    return total


def _latency_sec(state: Mapping[str, Any]) -> float | Measured[float]:
    """Includes a clarification wait. Unrounded; rendering belongs to ``Measured``."""
    started = state.get("turn_started_at")
    if not isinstance(started, (int, float)) or isinstance(started, bool):
        return Measured.unmeasured(
            "turn_started_at is absent: no wrapped node ran, so the turn has no start"
        )
    return max(0.0, time.time() - float(started))


def _recorded_channels(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """One map for scoring and degradation, so they cannot disagree."""
    channels = state.get("channels")
    if not channels:
        return None
    return {
        key: fr.get("channels")
        for key, fr in channels.items()
        if isinstance(fr, Mapping)
    }


def _attempts(execution: Mapping[str, Any] | Any) -> list[Any]:
    """A passing sample must not mark the turn answered."""
    if not isinstance(execution, Mapping):
        return []
    return answering_attempts(list(execution.get("attempts") or ()))


def _path_signals(
    inputs: OutcomeInputs,
) -> tuple[str | None, str | None, str | None, str | None, bool, str | None]:
    """``terminal`` is passed on only when the ledger observed an ending, or an unmarked turn stays a crash."""
    execution = inputs.execution
    path_kind = inputs.path_kind
    has_sql = inputs.has_sql

    if path_kind == "crashed" or inputs.failed:
        return (
            None,
            inputs.failed_stage,
            inputs.error_type,
            None,
            has_sql,
            None,
        )

    if path_kind == "refuse":
        reason = inputs.terminal_reason
        if not isinstance(reason, str) or not reason:
            guard = inputs.guard or {}
            if not isinstance(guard, Mapping):
                raise AttributeError("guard must be a mapping")
            reason = "guard"
        return reason, None, None, GUARD_PUBLIC_MESSAGE, False, None

    if path_kind == "decline":
        reason = inputs.terminal_reason
        if not isinstance(reason, str) or not reason:
            reason = "no_schema_matched"
        return reason, None, None, None, False, None

    if path_kind == "answered":
        # The loop finishing is not an answer. The ledger's terminal decides.
        attempts = _attempts(execution)
        raw_terminal = execution.get("terminal")
        terminal = raw_terminal if isinstance(raw_terminal, str) else None
        # Cap first. Nested under "nothing passed", a cap with a passing attempt
        # would record answered. This reads the ledger's terminal, not a second derivation.
        if terminal == "capped":
            return ATTEMPT_CAP_REFUSED_BY, None, None, None, False, terminal
        if attempts and not any(attempt_field(a, "passed") is True for a in attempts):
            # A guardrail error and a layer refusal are different outcomes.
            # ``guardrail_errors`` is read, not recomputed.
            errors = execution.get("guardrail_errors")
            if isinstance(errors, int) and errors > 0:
                return GUARDRAIL_ERROR, Stage.check.value, None, None, False, terminal
            return GUARDRAIL_REFUSED_BY, None, None, None, False, terminal
        # This branch does not force ``has_sql``. Classification uses the ledger terminal.
        return None, None, None, None, has_sql, terminal

    # No path mark and no ledger ending. Classification then treats no SQL as a crash.
    return None, None, None, None, has_sql, None


def _extract_factory(
    *,
    outcome: Outcome,
    execution: Mapping[str, Any],
    usage: list[Mapping[str, Any]],
    latency: float | Measured[float],
    failed_stage: str | None,
    error_type: str | None,
) -> Any:
    # ``evicted`` is projected. Otherwise a char-budget drop never reaches the record.
    delivery_keys = {"context_hash", "delivery_hash", "tool_delivered", "evicted"}

    def extract(state: Mapping[str, Any], name: str) -> Any:
        if name == "outcome":
            return outcome.value
        if name == "execution":
            return execution
        if name == "guardrail_errors":
            return int(execution.get("guardrail_errors", 0))
        if name == "usage":
            return usage
        if name == "n_re_served":
            n = state.get("n_re_served")
            return 0 if n is None else int(n)
        if name == "failed_stage":
            return failed_stage
        if name == "error_type":
            return error_type
        if name == "generated_sql":
            return state.get("generated_sql")
        if name in (
            "run_id",
            "turn_id",
            "thread_id",
            "question_id",
            "db_id",
            "attempt_id",
            "corpus_content_hash",
            "prompt_set_hash",
            "settings_resolved",
            "guard",
            "rewrite",
            "crossings",
            # One decline outcome covers several causes.
            "terminal_reason",
            # Copied, not interpreted. See ADR 0013. ``terminal_reason`` is the decision.
            "abstention",
        ):
            return state.get(name)

        # Derived here. Nothing writes these onto state.
        if name == "latency_sec":
            return latency
        if name in ("cache_read_tokens", "cache_write_tokens"):
            return _cache_total(usage, name)
        if name == "assumptions":
            raw = state.get("assumptions")
            return [dict(item) for item in raw] if isinstance(raw, list) else []
        if name in ("guess_count", "ambiguous_guess_refusals"):
            raw = state.get(name)
            if isinstance(raw, bool) or not isinstance(raw, int):
                return 0
            return raw
        if name == "answer_tool_called":
            # Refuse, decline, clarification, cap and crash are not a missing call.
            if state.get("answer_tool_called") is True:
                return True
            if error_type == NO_ANSWER_CALL:
                return False
            return outcome not in (Outcome.answered, Outcome.no_sql)

        if name == "schemas":
            return state.get("schemas")

        if name in delivery_keys:
            delivery = state.get("delivery")
            if isinstance(delivery, Mapping):
                return delivery.get(name)
            return None

        retrieved = state.get("retrieved")
        if name == "channel_hits":
            channels = state.get("channels")
            if not channels:
                return None
            return {
                key: {
                    "queries": fr.get("queries"),
                    "hits": fr.get("hits"),
                    "channels": fr.get("channels"),
                }
                for key, fr in channels.items()
                if isinstance(fr, Mapping)
            }
        if name == "channel_scoring":
            return _recorded_channels(state)
        if name == "channel_degraded":
            # ``None`` when fan-out did not run. ``False`` would read absence as clean.
            channels = _recorded_channels(state)
            if channels is None:
                return None
            return channels_degraded(channels)
        # Absent when no cap fired. ``.get`` writes that null; a missing projection
        # would make a discarded gold table look never retrieved.
        if name in ("schema_ranking", "pulled_in", "lexical_coverage",
                    "budget_dropped", "budget_best_dropped_score"):
            if isinstance(retrieved, Mapping):
                return retrieved.get(name)
            return None

        return state.get(name)

    return extract


def classify_turn(inputs: OutcomeInputs) -> TurnOutcome:
    """A crash overrides, including a crash after a statement had passed."""
    refused_by, failed_stage, error_type, text, has_sql, terminal = _path_signals(inputs)

    outcome = classify_outcome(
        error=None,
        refused_by=refused_by,
        has_sql=has_sql,
        clarification_requested=inputs.clarification_requested,
        terminal=terminal,
    )

    if inputs.path_kind == "crashed" or inputs.failed:
        outcome = Outcome.crashed

    # So a crash is not stored beside ``terminal=answered``.
    execution = inputs.execution
    if outcome is Outcome.crashed and execution.get("terminal") != "crashed":
        execution = {**execution, "terminal": "crashed"}

    return TurnOutcome(
        outcome=outcome,
        refused_by=refused_by,
        failed_stage=failed_stage,
        error_type=error_type,
        text=text,
        execution=execution,
        rail_status=_final_status(inputs.path_kind, outcome),
    )


def finish(state: Mapping[str, Any]) -> dict[str, Any]:
    """``result_table`` and ``answer_text`` stay off the record."""
    state = normalised(state)
    inputs = OutcomeInputs.from_state(state)
    result = classify_turn(inputs)
    outcome = result.outcome
    execution = result.execution
    failed_stage = result.failed_stage
    error_type = result.error_type
    usage = _usage_for_turn(state)

    # Do not substitute a guard. The failed-open sentinel means the guard ran and
    # let the question through. Absence is the wiring failure.
    projected_state: dict[str, Any] = dict(state)
    projected_state["execution"] = execution
    projected_state["usage"] = usage
    if projected_state.get("n_re_served") is None:
        projected_state["n_re_served"] = 0
    # Do not substitute ``{}`` for ``settings_resolved``. An empty mapping is a
    # configuration of nulls, and the drift check would pass it. Absence stays absence.

    record = project(
        projected_state,
        extract=_extract_factory(
            outcome=outcome,
            execution=execution,
            usage=usage,
            latency=_latency_sec(state),
            failed_stage=failed_stage,
            error_type=error_type,
        ),
    )

    answer = {
        "outcome": outcome.value,
        "text": result.text,
        "failed_stage": failed_stage,
        "error_type": error_type,
        "refused_by": result.refused_by,
        "record": record,
        # ``None`` means no query ran, not an empty table.
        "result_table": state.get("result_table"),
        # From ``narrate``, not recomputed here. Distinct from ``text``.
        "answer_text": state.get("answer_text"),
    }
    # The one ``final`` row (ADR 0010). This node is unwrapped, so it emits itself.
    emit(
        kind="final",
        step="finish",
        status=result.rail_status,
        event_id=rail_event_id("finish", state),
        detail={"outcome": outcome.value, "failed_stage": failed_stage},
    )
    return {"answer": answer}


def _final_status(path_kind: Any, outcome: Outcome) -> str:
    """``decline`` is its own status. ``Outcome`` has no declined member, so this would say ``refused``."""
    if path_kind == "decline":
        return "declined"
    return {
        Outcome.answered: "ok",
        Outcome.clarification: "ok",
        # No status means "ended with no statement". ``ok`` plus ``detail.outcome``
        # keeps the distinction without a new wire value.
        Outcome.no_sql: "ok",
        Outcome.refused: "refused",
        Outcome.capped: "cap",
        Outcome.crashed: "error",
    }.get(outcome, "ok")
