"""``stamp`` — sole writer of ``answer`` (ADR 0005 §3.1 / §4.1)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from governed_bi.govern.guard import GUARD_PUBLIC_MESSAGE
from governed_bi.govern.layers import GUARDRAIL_REFUSED_BY
from governed_bi.govern.ledger import ExecutionRecord
from governed_bi.measure.degradation import facets_degraded
from governed_bi.register.quantity import Measured
from governed_bi.register.record import project
from governed_bi.register.stages import ATTEMPT_CAP_REFUSED_BY, Outcome, classify_outcome
from governed_bi.serve.events import emit, rail_event_id
from governed_bi.serve.ledger import answering_attempts, attempt_field, execution_from_attempts
from governed_bi.serve.state import cleared

__all__ = ["stamp"]


def _usage_for_turn(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project usage for the current turn only (``operator.add`` accumulates)."""
    turn_index = state.get("turn_index", 1)
    raw = state.get("usage") or []
    return [u for u in raw if isinstance(u, Mapping) and u.get("turn_index") == turn_index]


def _cache_total(usage: list[dict[str, Any]], field: str) -> int | Measured[int]:
    """Sum one cache-token field across this turn's usage rows, or *unmeasured*.

    Unmeasured when **no** row reported the field: a provider that reports no cache activity
    has said nothing about caching, and ``0`` there would be this code's claim wearing the
    provider's clothes. A row reporting an explicit ``0`` is a measurement and counts.
    """
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
    """Wall-clock seconds from the turn's first node to now, or *unmeasured*.

    ``wrap_node`` stamps ``turn_started_at``, so unmeasured is the hand-built-state case (a unit
    test calling ``stamp`` directly), and it says so rather than reporting 0.0. A clarified turn
    includes the human's thinking time deliberately — the field is how long the user waited.

    Unrounded: ``tools/check_measurement_locality.py`` refuses formatting outside
    ``register/quantity.py``. Presentation is ``Measured.render``'s job.
    """
    started = state.get("turn_started_at")
    if not isinstance(started, (int, float)) or isinstance(started, bool):
        return Measured.unmeasured(
            "turn_started_at is absent: no wrapped node ran, so the turn has no start"
        )
    return max(0.0, time.time() - float(started))


def _execution(state: Mapping[str, Any]) -> ExecutionRecord:
    """The turn's ``ExecutionRecord``, written on every path including "no SQL".

    ``terminal`` is never derived here from ``path_kind``: ``execution_from_attempts`` is the
    one derivation and it reads the attempts, so a turn that attempted nothing says ``no_sql``
    whether it was guard-blocked, declined or stubbed.
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
    """This turn's **answering** ledger rows.

    Filtered: ``sample`` rows share the ledger, and a passing sample row would make
    ``_path_signals`` report a turn as answered whose every ``run_query`` was refused.
    """
    if not isinstance(execution, Mapping):
        return []
    return answering_attempts(list(execution.get("attempts") or ()))


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
        # ledger decides: a turn whose every attempt was refused is a refusal, and a turn the
        # cap ended is `capped`. `has_sql` alone is not enough — it comes from the tool-call
        # *arguments*, so producing a string counted as producing an answer.
        execution = state.get("execution")
        attempts = _attempts(execution)
        terminal = execution.get("terminal") if isinstance(execution, Mapping) else None
        # The cap first, and on its own condition — nested inside the "no attempt passed"
        # branch it is unreachable on any turn where a statement ever succeeded, so a capped
        # turn with two passing attempts records `outcome: answered`. `execution_from_attempts`
        # decides this and here we read its verdict, so the two cannot disagree.
        if terminal == "capped":
            return ATTEMPT_CAP_REFUSED_BY, None, None, None, False
        if attempts and not any(attempt_field(a, "passed") is True for a in attempts):
            return GUARDRAIL_REFUSED_BY, None, None, None, False
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
    latency: float | Measured[float],
    failed_stage: str | None,
    error_type: str | None,
) -> Any:
    # ``evicted`` included, so the served record carries it too: it was reaching the eval row
    # and nothing else, which would have left ``runs/serve/*.jsonl`` with no trace that the
    # char budget dropped a licensed table before the model ever saw it.
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
            "knobs_resolved",
            "guard",
            "rewrite",
            "negative",
            "crossings",
            "licensed",
            # Copied and never interpreted: `stamp` reading it to adjust `outcome` would be
            # the control flow `reflect` is defined not to have.
            "reflect_verdict",
            # Why a decline declined. `outcome: "declined"` is one value for four different
            # engineering problems, so without this "routing found nothing" and "the join
            # graph is disconnected" are the same recorded row.
            "terminal_reason",
        ):
            return state.get(name)

        # The three cost fields, derived here rather than read off state — nothing writes them.
        if name == "latency_sec":
            return latency
        if name in ("cache_read_tokens", "cache_write_tokens"):
            return _cache_total(usage, name)

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
            # Null when the fan-out did not run, like the field it derives from: `False` there
            # is the degradation gate reading absence as clean.
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

    ``Session.turn`` writes :data:`~governed_bi.serve.state.RESET` to ``path_kind``, ``failure``
    and ``facets``, and the first two must be normalised here: their annotations are Unions, so
    the channel seeds ``MISSING`` and LangGraph assigns the first write raw (see
    :func:`~governed_bi.serve.state.cleared`). ``failure`` is the one that bites — a successful
    turn never writes it, so the bare sentinel made ``state.get("failure") is not None`` true on
    every successful first turn of a fresh thread. ``facets`` strips to ``dict`` and is never at
    risk; it stays in the tuple for symmetry.

    Normalised in ``stamp`` rather than in each reader because this is the only node that
    *interprets* these channels — every other reader compares them against known values, where
    an unrecognised string already behaves as "not terminal".
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
    # Attempts stay; rewrite terminal so outcome=crashed never sits beside
    # execution.terminal=answered (a careless reader would treat the crash as answered).
    if outcome is Outcome.crashed and execution.get("terminal") != "crashed":
        execution = {**execution, "terminal": "crashed"}
    usage = _usage_for_turn(state)

    # ``guard`` is Absence.never and must **not** be substituted here. Standing in
    # ``{"outcome": "error_failed_open"}`` fabricates a security event — that sentinel means the
    # guard ran, errored and let the question through, and it is what a reader counts to find
    # out whether the gate worked. (No quotability gate reads it; see ``guard.py::_bi_scope``.)
    # An absent guard stays absent; ``missing_required`` names it as the wiring failure it is.
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
            latency=_latency_sec(state),
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
        # On the `answer` and deliberately **not** in `record`: ADR 0006 §11 puts result rows in
        # the class the durable projection drops, and the audit log persists the record only.
        # `None` on every path that ran no query, which is a different fact from an empty table.
        "result_table": state.get("result_table"),
        # From ``narrate``; same class as `result_table` and out of the record for the same
        # reason. Distinct from `text` above, which is *system* copy: on an answered turn `text`
        # is null and this is set, on a refusal the other way round, and the client renders on
        # that asymmetry. Read from state and never recomputed — a second derivation here is how
        # the audit list and the answer card came to disagree about `answer_text`.
        "answer_text": state.get("answer_text"),
    }
    # The turn's one ``final`` event (ADR 0010 §1). Emitted here because ``stamp`` is the one
    # node deliberately left unwrapped, so ``wrap.py``'s emitter never sees it. Emitted after
    # ``answer`` is built and from ``answer``, so the row and the record cannot disagree.
    emit(
        kind="final",
        step="stamp",
        status=_final_status(path_kind, outcome),
        event_id=rail_event_id("stamp", state),
        detail={"outcome": outcome.value, "failed_stage": failed_stage},
    )
    return {"answer": answer}


def _final_status(path_kind: Any, outcome: Outcome) -> str:
    """The ``stamp`` row's status.

    ``path_kind`` is consulted first for exactly one distinction: :class:`Outcome` has no
    ``declined`` member, so a decline classifies as ``refused`` — right for measurement, wrong
    for a timeline where "no schema matched" and "the guard blocked this" differ.
    """
    if path_kind == "decline":
        return "declined"
    return {
        Outcome.answered: "ok",
        Outcome.clarification: "ok",
        Outcome.refused: "refused",
        Outcome.capped: "cap",
        Outcome.crashed: "error",
    }.get(outcome, "ok")
