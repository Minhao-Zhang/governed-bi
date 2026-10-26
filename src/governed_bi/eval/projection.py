"""Shape a served turn's final state into a measurement row (ADR 0005).

The only connector use is a read-only re-execution that prices an abstention.
That price is never folded into ``correct``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, SupportsInt

from governed_bi.eval.attribution import attribute
from governed_bi.eval.grade import grade_turn, result_fingerprint
from governed_bi.eval.replay import PINNED_SCHEMAS_KEY
from governed_bi.registry.quantity import Measured
from governed_bi.registry.stages import Outcome
from governed_bi.serve.messages import last_proposed_sql
from governed_bi.serve.nodes.finish import paused_record

__all__ = ["ASSUMPTION_BASES", "project_turn"]


#: Outcomes whose last proposed statement is priced and not scored. ``clarification``
#: stays out: the turn asked a question, so that statement is not what it discarded.
#: ``no_sql`` has no proposal to re-execute.
PRICED_ABSTENTIONS: frozenset[str] = frozenset({"capped", "refused"})


def _abstained_fingerprint(
    *,
    outcome: str,
    proposed_sql: str | None,
    connector: Any | None,
    order_sensitive: bool,
    already_executed: bool,
) -> str | None:
    """The proposal, not ``generated_sql``, which is only what the engine sent. Answered turns already have one."""
    if already_executed or outcome not in PRICED_ABSTENTIONS:
        return None
    if not proposed_sql or connector is None:
        return None
    try:
        cols, rows, _ = connector.execute(str(proposed_sql))
    except Exception:  # noqa: BLE001 — a statement that will not run has no fingerprint
        return None
    return result_fingerprint(list(cols), [list(r) for r in rows], order_sensitive=order_sensitive)


def _attempt_trace(execution: Any) -> list[dict[str, Any]]:
    """A refused row otherwise says governance declined and not which layer."""
    if not isinstance(execution, Mapping):
        return []
    trace: list[dict[str, Any]] = []
    for attempt in execution.get("attempts") or ():
        if not isinstance(attempt, Mapping):
            continue
        trace.append(
            {
                "layer": attempt.get("result_layer"),
                "reason_code": attempt.get("reason_code"),
                "passed": attempt.get("passed"),
                "path": attempt.get("path"),
                # Only when set, so a row without paging keeps the shape older artifacts have.
                **({"reads_page": True} if attempt.get("reads_page") else {}),
            }
        )
    return trace


def _routing_was_pinned(question: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    """Attachment alone counts a turn that ended before routing. Schemas alone would credit a live run."""
    pinned = [str(s) for s in (question.get(PINNED_SCHEMAS_KEY) or ())]
    if not pinned:
        return False
    return [str(s) for s in (record.get("schemas") or ())] == pinned


#: How many ranked entries the summaries keep. Queries stay whole; hits below
#: this rank are a replay of those queries, not carried on the row.
_RANK_KEPT = 10


def _number(value: Any) -> float | None:
    """A number, or ``None``. ``bool`` is not a number here (``True`` would read as 1.0)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _row_tokens(usage: Any) -> dict[str, int | None]:
    totals: dict[str, int | None] = {"input_tokens": 0, "output_tokens": 0}
    for entry in usage:
        for key in totals:
            value = entry.get(key) if isinstance(entry, Mapping) else None
            current = totals[key]
            if current is None or not isinstance(value, int) or isinstance(value, bool):
                totals[key] = None
            else:
                totals[key] = current + value
    return totals


def _row_latency_sec(record: Mapping[str, Any]) -> float | None:
    """Wall clock in seconds, or ``None`` when it was not measured.

    A ``Measured`` absence must not be stringified onto the row. ``json.dumps``
    would store the repr, and that string compares like a value.
    """
    value = record.get("latency_sec")
    if isinstance(value, Measured):
        return _number(value.value) if value.is_measured else None
    return _number(value)


def _schema_ranking(
    record: Mapping[str, Any], question: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Gold rank over every scored schema, from this turn. A separate no-model recall is a different draw. Empty is a
    measured zero; missing routing is ``None``."""
    ranking = record.get("schema_ranking")
    if not isinstance(ranking, (list, tuple)):
        return None
    pairs = [
        (str(pair[0]), _number(pair[1]))
        for pair in ranking
        if isinstance(pair, (list, tuple)) and len(pair) >= 2
    ]
    gold = str(question.get("db_id") or "")
    return {
        "n_scored": len(pairs),
        "gold_rank": next((i + 1 for i, (name, _) in enumerate(pairs) if name == gold), None),
        "gold_score": next((score for name, score in pairs if name == gold), None),
        "top": [[name, score] for name, score in pairs[:_RANK_KEPT]],
    }


def _channel_hits(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Per channel: the query, the hit count, and the top ids.

    Queries stay whole because a model sampled them. Scores and hits past the
    kept rank are a replay of those queries against the named corpus.
    """
    channels = record.get("channel_hits")
    if not isinstance(channels, Mapping):
        return None
    out: dict[str, Any] = {}
    for name, result in channels.items():
        if not isinstance(result, Mapping):
            continue
        hits = [h for h in (result.get("hits") or ()) if isinstance(h, Mapping)]
        out[str(name)] = {
            "queries": list(result.get("queries") or ()),
            "n_hits": len(hits),
            "top": [str(h.get("asset_id")) for h in hits[:_RANK_KEPT]],
        }
    return out


def _pulled_in(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """How many assets entered by reference closure, and which by the join walk.

    Connect ids stay in full. Which columns the closure pulled is dropped;
    the count is what remains.
    """
    pulled = record.get("pulled_in")
    if not isinstance(pulled, Mapping):
        return None
    connect = sorted(str(k) for k, v in pulled.items() if str(v) == "connect")
    return {
        "n_resolve": sum(1 for v in pulled.values() if str(v) == "resolve"),
        "n_connect": len(connect),
        "connect_ids": connect,
    }


def _guard_result(record: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any] | None:
    """The guard's result, including a clear one.

    A check that leaves a trace only when it fires cannot be told from one
    that was never wired. Free-text detail is dropped.
    """
    guard = record.get("guard")
    if not isinstance(guard, Mapping):
        guard = state.get("guard")
    if not isinstance(guard, Mapping):
        return None
    return {"outcome": guard.get("outcome"), "rule_id": guard.get("rule_id")}


def _int_or_absent(value: SupportsInt | str | None) -> int | None:
    """``int(value)``, or ``None`` when the field was never written.

    ``0`` is both a clean count and the shape of a missing field. Substituting
    it turns "nobody counted" into "nothing went wrong".
    """
    if value is None:
        return None
    return int(value)


#: Bases an assumption may carry (ADR 0018). A count is kept for each, including zero.
ASSUMPTION_BASES: tuple[str, ...] = ("layer", "convention", "guess", "found_in_query")


def _assumption_counts(record: Mapping[str, Any]) -> dict[str, int] | None:
    """Counts by basis, or ``None`` when ``assumptions`` was not written.

    An empty list is an answer that stated nothing. A missing list is a crash
    or an old run, and those two must not look the same on the row.
    """
    if "assumptions" not in record:
        return None
    assumptions = record["assumptions"]
    if not isinstance(assumptions, (list, tuple)):
        return None
    counts = dict.fromkeys(ASSUMPTION_BASES, 0)
    for entry in assumptions:
        if not isinstance(entry, Mapping):
            continue
        basis = entry.get("basis")
        if basis in counts:
            counts[str(basis)] += 1
    return counts


def project_turn(
    state: Mapping[str, Any],
    *,
    question: Mapping[str, Any],
    config: str,
    order_sensitive: bool = False,
    connector: Any = None,
) -> dict[str, Any]:
    answer = state.get("answer") or {}
    record = answer.get("record") if isinstance(answer, Mapping) else None
    if not isinstance(record, Mapping):
        record = {}
    # A paused turn is not a crash. `ask_user` writes no answer, so its record is
    # built here. A later decline is the same outcome with a record from ``finish``.
    interrupted = bool(state.get("__interrupt__")) and not answer
    if interrupted:
        outcome = Outcome.clarification.value
        # ``finish`` has not run, so build the record it would write from the paused state.
        record = paused_record(state)
    else:
        outcome = str(answer.get("outcome") or record.get("outcome") or "crashed")
    crashed = outcome == "crashed"

    delivery = state.get("delivery") or {}
    context_hash = None
    if isinstance(delivery, Mapping):
        context_hash = delivery.get("context_hash")
    if context_hash is None:
        context_hash = record.get("context_hash")

    delivery_hash = record.get("delivery_hash")
    tool_delivered = record.get("tool_delivered")
    if isinstance(delivery, Mapping):
        if delivery_hash is None:
            delivery_hash = delivery.get("delivery_hash")
        if tool_delivered is None:
            tool_delivered = delivery.get("tool_delivered")

    generated_sql = state.get("generated_sql") or record.get("generated_sql")
    pred_columns = None
    pred_rows = None
    if (
        outcome == "answered"
        and generated_sql
        and connector is not None
        and question.get("gold_sql")
    ):
        try:
            cols, rows, _ = connector.execute(str(generated_sql))
            pred_columns = list(cols)
            pred_rows = [list(r) for r in rows]
        except Exception:  # noqa: BLE001 — grade as missing prediction
            pred_columns, pred_rows = None, None

    gold_fp = question.get("gold_fingerprint")
    gold_columns = question.get("gold_columns")
    gold_rows = question.get("gold_rows")
    wants_gold_rows = gold_fp is None or pred_rows is not None
    if gold_rows is None and wants_gold_rows and connector is not None and question.get("gold_sql"):
        try:
            gcols, grows, _ = connector.execute(str(question["gold_sql"]))
            gold_columns = list(gcols)
            gold_rows = [list(r) for r in grows]
        except Exception:  # noqa: BLE001
            pass

    grade = grade_turn(
        outcome=outcome,
        pred_columns=pred_columns,
        pred_rows=pred_rows,
        gold_columns=list(gold_columns) if gold_columns is not None else None,
        gold_rows=list(gold_rows) if gold_rows is not None else None,
        gold_fingerprint=str(gold_fp) if gold_fp else None,
        order_sensitive=order_sensitive,
    )

    # Price a capped or refused proposal in its own field. Folding it into
    # ``correct`` would report an engine that commits to everything.
    computed_fp = _abstained_fingerprint(
        outcome=outcome,
        # From the transcript. ``generated_sql`` is only what the engine sent, null on a refusal.
        proposed_sql=last_proposed_sql(state.get("messages") or ()),
        connector=connector,
        order_sensitive=order_sensitive,
        already_executed=pred_columns is not None,
    )

    channel_scoring = record.get("channel_scoring")
    # Absent stays absent. ``int(x or 0)`` turns a missing count into the clean
    # value, and the checks then pass. ``0`` is also a real measurement.
    guardrail_errors = _int_or_absent(record.get("guardrail_errors"))
    n_re_served = _int_or_absent(
        state.get("n_re_served") if state.get("n_re_served") is not None
        else record.get("n_re_served")
    )

    # Absent stays absent. ``{}`` would read to ``measure.checks`` as a real configuration in
    # which every setting resolved to None, so one config of empties would *pass* the check.
    settings = record.get("settings_resolved")
    if not isinstance(settings, Mapping):
        settings = state.get("settings_resolved")

    projected: dict[str, Any] = {
        "question_id": str(question["question_id"]),
        "config": config,
        "db_id": question.get("db_id"),
        "settings_resolved": dict(settings) if isinstance(settings, Mapping) else None,
        "outcome": outcome,
        # Propagated, never coerced. ``bool`` would mark a missing gold as a wrong answer.
        "correct": grade["correct"],
        # Both clarification endings count. This is not evidence the row went
        # unwritten; that check reads whether the record fields were written.
        "clarified": outcome == Outcome.clarification.value,
        "refused": outcome == Outcome.refused.value,
        "crashed": crashed,
        "quality_flags": list(question.get("quality_flags") or ()),
        "generated_sql": generated_sql,
        "gold_sql": question.get("gold_sql"),
        "gold_fingerprint": grade.get("gold_fingerprint"),
        "pred_fingerprint": grade.get("pred_fingerprint"),
        "grade_detail": grade.get("detail"),
        "context_hash": context_hash,
        "channel_scoring": channel_scoring,
        # ``None`` when unrecorded, not ``False``. ``False`` is the clean value.
        "channel_degraded": (
            None if record.get("channel_degraded") is None
            else bool(record.get("channel_degraded"))
        ),
        # Without these, a miss does not say whether routing or generation failed,
        # and a crash has no stage. An absent reach must not read as zero.
        "error_type": record.get("error_type") or state.get("failure", {}).get("error_type")
        if isinstance(state.get("failure"), Mapping)
        else record.get("error_type"),
        "corpus_content_hash": record.get("corpus_content_hash"),
        "prompt_set_hash": record.get("prompt_set_hash"),
        "context_evicted": (delivery.get("evicted") if isinstance(delivery, Mapping) else None),
        "delivery_hash": delivery_hash,
        # ``None`` means the agent loop never ran, which is different from ``{}``.
        "tool_delivered": dict(tool_delivered) if isinstance(tool_delivered, Mapping) else None,
        "retrieved_tables": list(record.get("retrieved_tables") or ()),
        "schemas": list(record.get("schemas") or ()),
        "schema_ranking": _schema_ranking(record, question),
        "channel_hits": _channel_hits(record),
        "pulled_in": _pulled_in(record),
        # A float or ``None``, never ``0.0`` for unmeasured. An embedder scores every asset above zero.
        "lexical_coverage": _number(record.get("lexical_coverage")),
        "budget_dropped": record.get("budget_dropped"),
        "budget_best_dropped_score": record.get("budget_best_dropped_score"),
        "crossings": (
            list(crossings)
            if isinstance(crossings := record.get("crossings"), (list, tuple))
            else None
        ),
        "guard": _guard_result(record, state),
        # Whether the shortlist ran, not whether a pin was attached. A partial
        # pin is not the shortlist that was pinned, so it reads false.
        "routing_pinned": _routing_was_pinned(question, record),
        "attempts": _attempt_trace(record.get("execution")),
        "computed_fingerprint": computed_fp,
        "computed_correct": (
            None if computed_fp is None or not gold_fp else computed_fp == str(gold_fp)
        ),
        "terminal_reason": record.get("terminal_reason"),
        # What the abstention policy decided, and the facts it asked about (ADR 0013).
        # ``terminal_reason`` is only the reason. ``None`` means the turn ended
        # before the node; a disabled policy is recorded, not silent.
        "abstention": record.get("abstention"),
        "usage": list(record.get("usage") or ()),
        # Totals beside the row. ``None`` when any call went uncounted, so a partial sum is not a cheap turn.
        **_row_tokens(record.get("usage") or ()),
        "latency_sec": _row_latency_sec(record),
        "guardrail_error": None if guardrail_errors is None else guardrail_errors > 0,
        "re_served": None if n_re_served is None else n_re_served > 0,
        "refused_by": answer.get("refused_by") if isinstance(answer, Mapping) else None,
        "failed_stage": answer.get("failed_stage") if isinstance(answer, Mapping) else None,
        # Missing list stays ``None``. Zero is an answer that named none of that basis.
        "assumption_counts": _assumption_counts(record),
        "guess_count": _int_or_absent(record.get("guess_count")),
        "ambiguous_guess_refusals": _int_or_absent(record.get("ambiguous_guess_refusals")),
    }

    # Own field, not `error_type`. That name is the exception class of a crash.
    # This label is for a completed wrong answer. Merging them would mark
    # wrong answers as crashes.
    cause = attribute(projected)
    projected["failure_cause"] = None if cause is None else cause.value

    return projected

