"""``stamp`` — sole writer of ``answer`` (ADR 0005 §3.1 / §4.1)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from governed_bi.govern.guard import GUARD_PUBLIC_MESSAGE
from governed_bi.govern.ledger import ExecutionRecord, execution_record
from governed_bi.register.record import project
from governed_bi.register.stages import Outcome, classify_outcome

__all__ = ["stamp"]


def _usage_for_turn(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project usage for the current turn only (``operator.add`` accumulates)."""
    turn_index = state.get("turn_index", 1)
    raw = state.get("usage") or []
    return [u for u in raw if isinstance(u, Mapping) and u.get("turn_index") == turn_index]


def _execution(state: Mapping[str, Any], path_kind: str | None) -> ExecutionRecord:
    existing = state.get("execution")
    if isinstance(existing, Mapping) and "attempts" in existing:
        return existing  # type: ignore[return-value]
    if path_kind == "answered":
        terminal: str = "answered"
    elif path_kind in ("refuse", "decline"):
        terminal = "refused"
    else:
        terminal = "no_sql"
    return execution_record([], terminal)  # type: ignore[arg-type]


def _path_signals(
    state: Mapping[str, Any],
) -> tuple[str | None, str | None, str | None, str | None, bool]:
    """Return ``(refused_by, failed_stage, error_type, text, has_sql)``."""
    path_kind = state.get("path_kind")
    failure = state.get("failure")
    generated_sql = state.get("generated_sql")
    has_sql = bool(generated_sql)

    if path_kind == "crashed" or failure is not None:
        stage = failure.get("stage") if isinstance(failure, Mapping) else None
        err = failure.get("error_type") if isinstance(failure, Mapping) else None
        return (
            None,
            stage if isinstance(stage, str) else None,
            err if isinstance(err, str) else None,
            None,
            has_sql,
        )

    if path_kind == "refuse":
        reason = state.get("terminal_reason")
        if not isinstance(reason, str) or not reason:
            guard = state.get("guard") or {}
            reason = "guard" if guard.get("outcome") == "blocked" else "negative_example"
        return reason, None, None, GUARD_PUBLIC_MESSAGE, False

    if path_kind == "decline":
        reason = state.get("terminal_reason")
        if not isinstance(reason, str) or not reason:
            reason = "no_schema_matched"
        return reason, None, None, None, False

    if path_kind == "answered":
        # ``classify_outcome`` uses has_sql as "answered"; SQL presence is still
        # projected from ``generated_sql`` on the register record.
        return None, None, None, None, True

    # Unmarked path: let classify_outcome fall through (no SQL ⇒ crashed).
    return None, None, None, None, has_sql


def _extract_factory(
    *,
    outcome: Outcome,
    execution: ExecutionRecord,
    usage: list[dict[str, Any]],
    failed_stage: str | None,
    error_type: str | None,
) -> Any:
    delivery_keys = {"context_hash", "delivery_hash", "tool_delivered"}

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
        if name == "final_sql_source":
            return state.get("final_sql_source")
        if name in (
            "run_id",
            "turn_id",
            "thread_id",
            "question_id",
            "db_id",
            "attempt_id",
            "corpus_content_hash",
            "prompt_set_hash",
            "knobs_resolved",
            "guard",
            "rewrite",
            "negative",
            "crossings",
            "licensed",
            "cache_read_tokens",
            "cache_write_tokens",
            "cost_est_usd",
            "latency_sec",
        ):
            return state.get(name)

        if name == "schemas":
            return state.get("schemas")

        if name in delivery_keys:
            delivery = state.get("delivery")
            if isinstance(delivery, Mapping):
                return delivery.get(name)
            return None

        retrieved = state.get("retrieved")
        if name == "facet_hits":
            facets = state.get("facets")
            if not facets:
                return None
            return {
                key: {
                    "queries": fr.get("queries"),
                    "hits": fr.get("hits"),
                    "channels": fr.get("channels"),
                }
                for key, fr in facets.items()
                if isinstance(fr, Mapping)
            }
        if name == "facet_channels":
            facets = state.get("facets")
            if not facets:
                return None
            return {
                key: fr.get("channels")
                for key, fr in facets.items()
                if isinstance(fr, Mapping)
            }
        if name in ("schema_ranking", "pulled_in", "lexical_coverage"):
            if isinstance(retrieved, Mapping):
                return retrieved.get(name)
            return None

        return state.get(name)

    return extract


def stamp(state: Mapping[str, Any]) -> dict[str, Any]:
    """Build the turn ``Answer`` and the register projection. Sole writer of ``answer``."""
    path_kind = state.get("path_kind")
    refused_by, failed_stage, error_type, text, has_sql = _path_signals(state)

    outcome = classify_outcome(
        error=None,
        refused_by=refused_by,
        has_sql=has_sql,
        clarification_requested=bool(state.get("clarification_requested")),
    )

    # Crash with a failed stage but no refused_by: classify_outcome already returns
    # crashed when has_sql is false. Keep outcome as stamped.
    if path_kind == "crashed" or state.get("failure") is not None:
        outcome = Outcome.crashed

    execution = _execution(state, path_kind if isinstance(path_kind, str) else None)
    usage = _usage_for_turn(state)

    # Absence.never: guard must be a real value on every path.
    guard = state.get("guard")
    if not isinstance(guard, Mapping):
        guard = {"outcome": "error_failed_open", "rule_id": None, "detail": None}

    projected_state: dict[str, Any] = dict(state)
    projected_state["guard"] = guard
    projected_state["execution"] = execution
    projected_state["usage"] = usage
    if projected_state.get("n_re_served") is None:
        projected_state["n_re_served"] = 0
    if projected_state.get("knobs_resolved") is None:
        projected_state["knobs_resolved"] = {}

    record = project(
        projected_state,
        extract=_extract_factory(
            outcome=outcome,
            execution=execution,
            usage=usage,
            failed_stage=failed_stage,
            error_type=error_type,
        ),
    )

    answer = {
        "outcome": outcome.value,
        "text": text,
        "failed_stage": failed_stage,
        "error_type": error_type,
        "refused_by": refused_by,
        "record": record,
    }
    return {"answer": answer}
