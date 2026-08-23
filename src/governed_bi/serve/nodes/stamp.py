"""``stamp`` — sole writer of ``answer`` (ADR 0005 §3.1 / §4.1)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from governed_bi.govern.guard import GUARD_PUBLIC_MESSAGE
from governed_bi.govern.layers import GUARDRAIL_ERROR, GUARDRAIL_REFUSED_BY
from governed_bi.govern.ledger import ExecutionRecord
from governed_bi.measure.degradation import facets_degraded
from governed_bi.register.quantity import Measured
from governed_bi.register.record import project
from governed_bi.register.stages import ATTEMPT_CAP_REFUSED_BY, Outcome, Stage, classify_outcome
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
    execution: Mapping[str, Any],
) -> tuple[str | None, str | None, str | None, str | None, bool, str | None]:
    """Return ``(refused_by, failed_stage, error_type, text, has_sql, terminal)``.

    ``terminal`` is the ledger's own verdict, and it is handed to
    :func:`~governed_bi.register.stages.classify_outcome` **only** on the paths where the ledger
    observed an ending. ``None`` everywhere else, which is what keeps an unmarked or crashed turn
    classifying as ``crashed`` rather than as a turn that merely ran no statement.

    ``execution`` is passed in rather than re-read from ``state``: :func:`_execution` is the one
    place that substitutes ``execution_from_attempts(())`` for a turn nothing wrote a ledger for
    — the ``--no-model`` stub is one — and reading ``state["execution"]`` here instead would see
    ``None`` and report a stubbed turn as a crash.
    """
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
            None,
        )

    if path_kind == "refuse":
        reason = state.get("terminal_reason")
        if not isinstance(reason, str) or not reason:
            guard = state.get("guard") or {}
            reason = "guard" if guard.get("outcome") == "blocked" else "negative_example"
        return reason, None, None, GUARD_PUBLIC_MESSAGE, False, None

    if path_kind == "decline":
        reason = state.get("terminal_reason")
        if not isinstance(reason, str) or not reason:
            reason = "no_schema_matched"
        return reason, None, None, None, False, None

    if path_kind == "answered":
        # The agent loop finished, which is not the same as the turn having answered. The
        # ledger decides: a turn whose every attempt was refused is a refusal, and a turn the
        # cap ended is `capped`. `has_sql` alone is not enough — it comes from the tool-call
        # *arguments*, so producing a string counted as producing an answer.
        attempts = _attempts(execution)
        raw_terminal = execution.get("terminal")
        terminal = raw_terminal if isinstance(raw_terminal, str) else None
        # The cap first, and on its own condition — nested inside the "no attempt passed"
        # branch it is unreachable on any turn where a statement ever succeeded, so a capped
        # turn with two passing attempts records `outcome: answered`. `execution_from_attempts`
        # decides this and here we read its verdict, so the two cannot disagree.
        if terminal == "capped":
            return ATTEMPT_CAP_REFUSED_BY, None, None, None, False, terminal
        if attempts and not any(attempt_field(a, "passed") is True for a in attempts):
            # Nothing passed. *Why* nothing passed decides the outcome, and the two answers are
            # different engineering problems: the layer stack objecting is the product working,
            # a swallowed exception inside `check()` is our bug. `Outcome` requires they stay
            # apart and this branch used to collapse them, so a systematically broken `check()`
            # read as an arm that refused everything with `crash_rate == 0` — the exact symptom
            # `govern.layers.GUARDRAIL_ERROR` documents. `guardrail_errors` is derived by
            # `execution_from_attempts`, so as with the cap above we read its verdict rather
            # than re-deriving one that could disagree. 2026-08-10 audit (C3).
            errors = execution.get("guardrail_errors")
            if isinstance(errors, int) and errors > 0:
                return GUARDRAIL_ERROR, Stage.check.value, None, None, False, terminal
            return GUARDRAIL_REFUSED_BY, None, None, None, False, terminal
        # No answering attempt at all, so no governed statement ran. `has_sql` is read off
        # `generated_sql`, which `agent_core` writes only from an *executed* ledger row, so it is
        # false here and the turn classifies `Outcome.no_sql` from the ledger's own `terminal`.
        #
        # This line used to `return ..., True` — `has_sql` hardcoded, `generated_sql` ignored —
        # and its comment said the model had answered from the delivered context. Three paths
        # produce exactly these signals and nothing in the record separates them
        # (`Outcome.no_sql`'s docstring names all three), so the fall-through was picking the
        # benign one. Measured 2026-08-18: a model declining in prose because the corpus defined
        # none of the question's terms recorded `outcome: answered` beside `ledger: no_sql` and
        # `generated_sql: null`; and across the 9,459 rows in `runs/eval/*.jsonl` all 23
        # `answered`-with-no-statement turns carry a **null** `answer_text`, so the case the
        # comment defended has no evidence behind it. What holds of all three is that no governed
        # statement ran, and that is what is recorded now.
        return None, None, None, None, has_sql, terminal

    # Unmarked path: no ledger verdict is handed over, so classify_outcome falls through
    # (no SQL ⇒ crashed). A turn nothing marked has not been observed ending.
    return None, None, None, None, has_sql, None


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
    # and nothing else, which left the served turn record with no trace that the char budget
    # dropped a licensed table before the model ever saw it.
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
            # The abstention policy's verdict and its evidence (ADR 0013). Copied and never
            # interpreted, like `reflect_verdict` above and for the inverse reason: `abstain`
            # has *already* decided, and re-reading its verdict here to adjust `outcome` would
            # be two answers to "did this turn withhold". The one answer is `terminal_reason`,
            # which the node writes into the same channel `route` and `connect` write.
            "abstention",
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
        # The keys the register reads straight off `retrieved`. Both budget witnesses are here
        # from 2026-08-12: `merge_delta` stopped `resolve` destroying them, but a key that
        # survives to `stamp` and is not projected reaches no artifact, so "the cap discarded
        # the gold table" was still unanswerable from a record. They are `NotRequired` on
        # `RetrievalResult` — absent when no cap bit — and `.get` writes the null the register
        # declares for that.
        if name in ("schema_ranking", "pulled_in", "lexical_coverage",
                    "budget_dropped", "budget_best_dropped_score"):
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
    # The ledger first, because the classification now reads it. One derivation, shared with the
    # projection below, so the `outcome` a reader sees and the `execution` printed beside it came
    # out of the same record.
    execution = _execution(state)
    refused_by, failed_stage, error_type, text, has_sql, terminal = _path_signals(state, execution)

    outcome = classify_outcome(
        error=None,
        refused_by=refused_by,
        has_sql=has_sql,
        clarification_requested=bool(state.get("clarification_requested")),
        terminal=terminal,
    )

    # Crash with a failed stage but no refused_by: classify_outcome already returns crashed when
    # has_sql is false and no ledger verdict was handed over. Keep outcome as stamped.
    if path_kind == "crashed" or state.get("failure") is not None:
        outcome = Outcome.crashed

    # Attempts stay; rewrite terminal so outcome=crashed never sits beside
    # execution.terminal=answered (a careless reader would treat the crash as answered).
    if outcome is Outcome.crashed and execution.get("terminal") != "crashed":
        execution = {**execution, "terminal": "crashed"}
    usage = _usage_for_turn(state)

    # ``guard`` is Absence.never and must **not** be substituted here. Standing in
    # ``{"outcome": "error_failed_open"}`` fabricates a security event — that sentinel means the
    # guard ran, errored and let the question through, and it is what a reader counts to find
    # out whether the gate worked. (No quotability gate reads it: ``measure/gates.py`` declares
    # no gate over ``guard``. The rule the sentinel stands in for is
    # ``govern/guard.BI_SCOPE_RULE_ID`` == ``g_bi_scope``, ``guard.py:160``.)
    # An absent guard stays absent; ``missing_required`` names it as the wiring failure it is.
    projected_state: dict[str, Any] = dict(state)
    projected_state["execution"] = execution
    projected_state["usage"] = usage
    if projected_state.get("n_re_served") is None:
        projected_state["n_re_served"] = 0
    # ``knobs_resolved`` gets the same treatment as ``guard`` above, and for the same reason —
    # it used to be substituted with ``{}`` here, which is the one case ``measure.gates`` names
    # as the thing it must never see: ``{}`` is a ``Mapping``, so the drift gate reads it as a
    # real configuration in which every knob resolved to ``None``, every row's signature is
    # identical, and **an arm of empties passes**. `measure/gates.py::_knobs_resolved_gate` says
    # so in as many words ("Absent ``knobs_resolved`` is unmeasured, not passing"), and
    # `harness.py` carries the same "absent stays absent" comment, while this line defeated
    # both. Absent now reaches
    # ``project`` as absent, so ``Absence.never`` reports ``missing_required`` and the gate
    # returns ``cannot_evaluate`` — which is what a turn whose knobs were never wired *is*.
    # Found by the 2026-08-10 audit (C5).

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
        # The rail's status vocabulary is closed and shared with the client
        # (``ui/lib/steps.ts``'s ``GovEvent["status"]``), and no member of it means "ended with
        # no statement" — ``refused`` and ``error`` would each claim something this turn did not
        # do. ``ok`` with the outcome in ``detail`` is the honest pair: the client labels the row
        # from ``detail.outcome`` (``outcomeLabel``), so the distinction survives without a wire
        # value being invented for it.
        Outcome.no_sql: "ok",
        Outcome.refused: "refused",
        Outcome.capped: "cap",
        Outcome.crashed: "error",
    }.get(outcome, "ok")
