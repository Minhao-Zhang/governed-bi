"""``stamp`` — sole writer of ``answer`` (ADR 0005 §3.1 / §4.1)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from governed_bi.govern.guard import GUARD_PUBLIC_MESSAGE
from governed_bi.govern.layers import GUARDRAIL_REFUSED_BY
from governed_bi.govern.ledger import ExecutionRecord
from governed_bi.measure.degradation import facets_degraded
from governed_bi.register.record import project
from governed_bi.register.stages import ATTEMPT_CAP_REFUSED_BY, Outcome, classify_outcome
from governed_bi.serve.state import cleared
from governed_bi.serve.tools import attempt_field, execution_from_attempts

__all__ = ["stamp"]


def _usage_for_turn(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project usage for the current turn only (``operator.add`` accumulates)."""
    turn_index = state.get("turn_index", 1)
    raw = state.get("usage") or []
    return [u for u in raw if isinstance(u, Mapping) and u.get("turn_index") == turn_index]


def _execution(state: Mapping[str, Any]) -> ExecutionRecord:
    """The turn's ``ExecutionRecord``, written on every path including "no SQL".

    ``path_kind`` used to decide ``terminal`` here — a second implementation of the
    decision ``execution_from_attempts`` makes, and it disagreed with the ledger in the
    same direction: ``answered`` for any turn the agent node reached, empty attempts and
    all. There is one derivation now and it reads the attempts, so a turn that attempted
    nothing says ``no_sql`` whether it was guard-blocked, declined or stubbed.
    """
    existing = state.get("execution")
    if isinstance(existing, Mapping) and "attempts" in existing:
        return existing  # type: ignore[return-value]
    return execution_from_attempts(())  # type: ignore[return-value]


def _facet_channels(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """``{facet: {channel: state}}`` as the record carries it, or ``None``.

    One reader for two register fields: ``facet_degraded`` must be derived from exactly the
    mapping ``facet_channels`` publishes, or the record could report a degradation the
    channel states beside it do not show.
    """
    facets = state.get("facets")
    if not facets:
        return None
    return {
        key: fr.get("channels")
        for key, fr in facets.items()
        if isinstance(fr, Mapping)
    }


def _attempts(execution: Mapping[str, Any] | Any) -> list[Any]:
    if not isinstance(execution, Mapping):
        return []
    return list(execution.get("attempts") or ())


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
        # The agent loop finished, which is not the same as the turn having answered. The
        # ledger decides: a turn whose every attempt was refused is a refusal, and a turn
        # the cap ended is `capped`. `has_sql` came from the tool-call *arguments*, so
        # producing a string counted as producing an answer and `outcome: answered` sat
        # beside `passed: false` — the crash-counted-as-refusal inversion, reversed.
        execution = state.get("execution")
        attempts = _attempts(execution)
        terminal = execution.get("terminal") if isinstance(execution, Mapping) else None
        if attempts and not any(attempt_field(a, "passed") is True for a in attempts):
            reason = ATTEMPT_CAP_REFUSED_BY if terminal == "capped" else GUARDRAIL_REFUSED_BY
            return reason, None, None, None, False
        # No attempt at all: the model answered from the delivered context (or the F3 stub
        # did). That is `answered` with `generated_sql` null, which the register declares.
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
            # Why a decline declined. `outcome: "declined"` is one value for four
            # different engineering problems, and this lived in graph state only -- so
            # "routing found nothing" and "the join graph is disconnected" were the same
            # recorded row.
            "terminal_reason",
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
            return _facet_channels(state)
        if name == "facet_degraded":
            # Null when the fan-out did not run, like the field it is derived from: `False`
            # there would be the degradation gate reading absence as clean, which is the
            # defect the field was added to stop.
            channels = _facet_channels(state)
            if channels is None:
                return None
            return facets_degraded(channels)
        if name in ("schema_ranking", "pulled_in", "lexical_coverage"):
            if isinstance(retrieved, Mapping):
                return retrieved.get(name)
            return None

        return state.get(name)

    return extract


def stamp(state: Mapping[str, Any]) -> dict[str, Any]:
    """Build the turn ``Answer`` and the register projection. Sole writer of ``answer``.

    **The three reset-bearing channels are normalised first, and that is not defensive
    programming.** ``Session.turn`` writes :data:`~governed_bi.serve.state.RESET` to
    ``path_kind``, ``failure`` and ``facets``, and LangGraph assigns a channel's **first** value
    without calling its reducer — so on a turn where nothing else writes one, the bare sentinel
    reaches this function. ``failure`` is the common case, because a turn that succeeds never
    writes it: ``state.get("failure") is not None`` was then true for **every** successful turn,
    and this function stamped ``outcome: "crashed"`` on all of them. ``facets`` was a latent
    crash of the same shape — a guard-refused turn never runs the fan-out, and
    ``"reset".items()`` raises here, in the one node that is deliberately unwrapped.

    Normalised in ``stamp`` rather than in each reader because this is the only node that
    *interprets* these channels. Every other reader compares them against known values, where
    an unrecognised string already behaves as "not terminal", which is correct for a fresh turn.
    """
    state = {**state, **{k: cleared(state.get(k)) for k in ("path_kind", "failure", "facets")}}
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

    execution = _execution(state)
    usage = _usage_for_turn(state)

    # ``guard`` is Absence.never and it is **not** substituted here. Three lines used to
    # put ``{"outcome": "error_failed_open"}`` in place of a missing guard, under a comment
    # naming the invariant they broke: that sentinel means the guard ran, errored and let
    # the question through, ``register/record.py`` gates on it, and inventing it for a guard
    # that never ran fabricates a security event — the quotability gate then refuses a run
    # for something that did not happen. An absent guard stays absent, and
    # ``missing_required`` names it, which is a wiring failure reported as one.
    projected_state: dict[str, Any] = dict(state)
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
