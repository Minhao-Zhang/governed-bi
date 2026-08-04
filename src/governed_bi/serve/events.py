"""The live stage-event stream — one place that knows the wire shape (ADR 0010).

A turn takes 30–120 seconds and used to show nothing until it ended. This module is what makes
it visible: every rail, every tool and every governance verdict is written to LangGraph's
``custom`` stream as it happens, in the envelope the frontend already validates.

**Why the payload derivation lives here and not in the callers.** ``wrap.py`` is a generic
wrapper — it knows "a node ran and returned this dict" and nothing about what any particular
node means. Putting :func:`rail_observation` here keeps that boundary: the wrapper reports,
this module interprets. It is also the only way one emitter can cover thirteen rails without a
per-node ``writer(...)`` call, and a per-node call is a call somebody forgets — the
missing-call failure mode is a step that silently never appears.

**Three rules, and each one is a defect this repository has already paid for.**

*Status is observed, never declared.* Every status is read out of what the node actually
returned. A status computed from configuration makes a broken run and a clean run look
identical, which is the ``_channels_for`` defect one layer out.

*The stream carries what the record carries.* ADR 0006 §11 sets the ledger's retention by
vocabulary class — closed vocabularies and numbers kept, the statement kept as the **executed**
SQL plus its digest, driver error text and result rows dropped, exceptions as
``type(exc).__name__`` and never ``str(exc)``. The live stream is a second projection of the
same turn and the frontend's own module claims "live == audit", so it obeys the same rule.
``execute`` reports ``AttemptRecord.executed_sql``, the string the ledger hashes, not the
model's raw argument: a live view showing one statement while the audit shows another is
exactly what that rule exists to prevent.

*Emission cannot change a turn's outcome.* :func:`emit` swallows. A stream event that fails to
send is not a governance event that failed to happen, and an observability layer that can fail
a turn is worse than one that can go quiet. ``get_stream_writer()`` also raises
``RuntimeError`` outside a runnable context, which is ``eval/harness.py`` and
``python -m governed_bi.serve``, and those callers must keep working untouched. The cost of
swallowing is that a broken emitter is invisible, so ``tests/test_stream_events.py`` asserts
the payload builder over every stage and status rather than leaving production to notice.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import Any

# Imported at module scope, not inside :func:`emit`. ``langgraph dev`` installs `blockbuster`,
# which keeps ``os.getcwd`` armed, and Python's import machinery calls it — so a function-level
# import reached from a node is the exact shape that turned the first request into
# ``BlockingError: Blocking call to os.getcwd`` with no frame of ours in the traceback
# (``api/graph_app.py``'s ``_warm_imports`` was added for it). A ``sys.modules`` hit would
# short-circuit before the path machinery in practice, but "in practice" is the wrong standard
# for a call that now runs twice per node.
from langgraph.config import get_stream_writer

__all__ = [
    "emit",
    "rail_observation",
    "rail_event_id",
    "tool_event_id",
    "FIRST_STAGE",
    "TERMINAL_HANDLERS",
    "silenced_by_terminal_state",
]

#: Monotonic **within this process**, which is all this number can honestly be — and the wire
#: contract says exactly that: ``seq`` orders events that arrived together in one stream, and the
#: client owns a row's position *across* streams.
#:
#: That division is not fastidiousness. A clarification splits a turn into two SSE connections
#: with a human's thinking time between them, and ``langgraph dev`` reloads on file save, so the
#: counter can restart mid-turn: a client sorting globally on ``seq`` then rendered ``stamp``
#: above ``guard``. Per-turn numbering fixes nothing — the resume would restart at 1 too — which
#: is why the ordering guarantee belongs to the reader and not to this counter.
#:
#: ``itertools.count`` rather than an ``int`` and a lock because the five facet nodes run
#: concurrently in LangGraph's executor and ``next()`` on a count is a single C-level
#: operation. A ``+= 1`` here would drop numbers under exactly the fan-out this stream exists
#: to show.
_SEQ = itertools.count(1)

#: The stage that carries ``serve_path`` on its ``start``. The client reads it from the first
#: event of a turn, and ``accept`` is the first node on the only path that streams — a server
#: mounting the graph (ADR 0007 §2). A caller that builds its own turn has no ``accept`` and no
#: writer either, so nothing is lost.
FIRST_STAGE = "accept"

#: Stages that run **regardless of the incoming ``path_kind``**, and so must never be silenced
#: by it. Everything else that finds a terminal ``path_kind`` returns ``{}`` — it did not run,
#: and a ``start``/``ok`` pair for it would claim a step happened.
#:
#: ``refuse`` and ``decline`` run *because* the turn ended; silencing them would drop the only
#: row that says why.
#:
#: **``accept`` is here for a different and much less obvious reason, and its absence was a
#: bug.** ``path_kind`` is a checkpointed channel, and ``accept`` is the node that *clears* it —
#: ``Session.turn`` writes ``RESET`` and ``accept`` returns it. So on turn N+1 of a thread whose
#: turn N was refused, declined or crashed, ``accept`` reads its predecessor's terminal value on
#: entry, and the check silenced it. Measured: two turns on one thread, turn 1 declining, and
#: turn 2 emitted **no** ``accept`` events at all — losing the first row of the timeline *and*
#: the turn's only ``serve_path`` tag, which the wire contract says rides the first event. Every
#: follow-up question after a decline was affected, and declines are not rare.
TERMINAL_HANDLERS = frozenset({"accept", "refuse", "decline"})

_TERMINAL_PATH_KINDS = frozenset({"refuse", "decline", "crashed"})


def emit(
    *,
    kind: str,
    step: str,
    status: str,
    event_id: str,
    detail: Mapping[str, Any] | None = None,
    serve_path: str | None = None,
) -> None:
    """Write one event to the ``custom`` stream. Never raises.

    ``event_id`` rather than ``id`` because the wire key is ``id`` and shadowing the builtin
    in a module every node imports is not worth the symmetry.
    """
    payload: dict[str, Any] = {
        "seq": next(_SEQ),
        "id": event_id,
        "kind": kind,
        "step": step,
        "status": status,
    }
    if detail:
        payload["detail"] = dict(detail)
    if serve_path:
        payload["serve_path"] = serve_path
    try:
        get_stream_writer()(payload)
    except Exception:  # noqa: BLE001 — see the module docstring's third rule
        return


def rail_event_id(stage: str, state: Mapping[str, Any]) -> str:
    """A rail's row id — stable across a resume replay.

    Keyed on ``turn_id`` and not on ``seq``. When ``ask_user`` interrupts, the pending node
    re-executes on resume, so its ``start`` is emitted a second time; a seq-derived id would
    have opened a second row and shown the same stage twice in one turn. ``accept`` mints
    ``turn_id`` and therefore does not have one yet, which is why the fallback exists — and it
    is safe, because ``accept`` cannot be the node a resume replays.
    """
    return f"{stage}:{state.get('turn_id') or 'accept'}"


def tool_event_id(stage: str, call_id: str) -> str:
    """A tool row's id. ``tool_call_id`` is already the key every durable thing a tool writes
    is filed under (``tool_delivered``, ``attempts_by_call``), so the timeline and the record
    agree on what one call was without a second identifier to reconcile."""
    return f"{stage}:{call_id}"


def silenced_by_terminal_state(stage: str, state: Mapping[str, Any]) -> bool:
    """Whether this node is about to no-op because the turn already ended.

    ``route``, ``resolve``, ``connect``, ``assemble`` and ``agent_core`` all open with
    ``if state.get("path_kind") in TERMINAL_PATH_KINDS: return {}``. Emitting for them anyway
    produces a ``start`` and an ``ok`` for work that did not happen, which is the declared-status
    defect wearing a different hat.

    The set it consults is the *exemption* list, and reading it off the incoming state is what
    made ``accept`` a special case — see :data:`TERMINAL_HANDLERS`. The rule to apply when adding
    a node: exempt it if it runs without consulting ``path_kind``, silence it if it returns
    ``{}`` when ``path_kind`` is terminal.
    """
    if stage in TERMINAL_HANDLERS:
        return False
    return state.get("path_kind") in _TERMINAL_PATH_KINDS


def rail_observation(stage: str, update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """``(status, detail)`` for a rail, read out of the update it returned.

    Nothing here consults configuration, the policy, or what the stage is *supposed* to do.
    Where a fact is absent the detail key is omitted rather than defaulted, because a zero
    that means "not observed" is the shape ``register/stages.py`` warns about for
    ``by_failed_stage``.
    """
    path_kind = update.get("path_kind")
    if path_kind == "crashed":
        failure = update.get("failure") or {}
        detail: dict[str, Any] = {}
        if isinstance(failure, Mapping) and failure.get("error_type"):
            detail["error_type"] = str(failure["error_type"])
        return "error", detail
    if path_kind == "refuse":
        return "refused", _reason(update)
    if path_kind == "decline":
        # A declining ``route`` or ``connect`` also carries the numbers it got as far as
        # computing, and dropping them made the row that most needs explaining the least
        # informative one: "No join path" with nothing beside it, where the same node on a
        # success reports ``n_crossings`` and ``n_licensed``.
        detail = _reason(update)
        handler = _DETAIL_BY_STAGE.get(stage)
        if handler is not None:
            _, observed = handler(update)
            detail = {**observed, **detail}
        return "declined", detail

    handler = _DETAIL_BY_STAGE.get(stage)
    if handler is not None:
        return handler(update)
    return "ok", {}


def _reason(update: Mapping[str, Any]) -> dict[str, Any]:
    """The refusal/decline reason, under **both** names, and that is deliberate.

    ``terminal_reason`` is what the state channel and the record call it, so a reader comparing
    the row against the audit record finds the same key. ``reason`` is what ADR 0010's own table
    declared and what the client renders. Emitting one meant the most important row on a failed
    turn rendered with no explanation at all — caught by review, four times over, because the
    engine and the contract had drifted from each other by one word.

    Both, rather than picking one and editing the other, because each name is correct in its own
    place and neither side should have to translate. They are the same string by construction.
    """
    reason = update.get("terminal_reason")
    if not reason:
        return {}
    return {"terminal_reason": str(reason), "reason": str(reason)}


def _accept(update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    turn_index = update.get("turn_index")
    return "ok", ({"turn_index": int(turn_index)} if isinstance(turn_index, int) else {})


def _guard(update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    guard = update.get("guard")
    if not isinstance(guard, Mapping):
        return "ok", {}
    outcome = guard.get("outcome")
    if outcome == "error_failed_open":
        # **Not ``ok``.** This sentinel means the guard ran, a rule *errored*, and the question
        # was let through anyway. ``register/record.py`` makes it a countable security event and
        # gates a run on it — so reporting it as a clean pass is the stream disagreeing with the
        # record about whether anything happened, on the one outcome where "nothing happened" is
        # the dangerous reading. ``error`` is the honest status: the gate did not do its job.
        return "error", {"gate": "error_failed_open"}
    if outcome == "blocked":
        detail: dict[str, Any] = {}
        # **``rule_id`` yes, ``detail`` no**, and that split is not this module's to make —
        # ``register/record.py`` already made it for the ``guard`` field: *"The rule_id is
        # closed-vocabulary; the detail is free text and dropped."* So the id is a fact the
        # record ships to this same client in ``answer.record`` and the stream discloses nothing
        # new by naming it, while ``GuardVerdict.detail`` is marked "Ledger only. Never
        # surfaced" against rule-probing and must not appear here. Reading the retention class
        # off the register is the point: a second answer to "what may this carry" is how the two
        # channels drift.
        if guard.get("rule_id"):
            detail["rule_id"] = str(guard["rule_id"])
        return "blocked", detail
    return "ok", {}


def _negative(update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """``hit`` / ``miss`` / ``ok``.

    The third case is the one worth naming: the gate ships **disabled** (``negative_tau`` is
    UNSET until a negative corpus exists), and reporting a disabled gate as ``miss`` would
    claim it looked and found nothing. It did not look. ``ok`` with the outcome in the detail
    says so.
    """
    negative = update.get("negative")
    if not isinstance(negative, Mapping):
        return "ok", {}
    outcome = negative.get("outcome")
    if outcome == "hit":
        matched = negative.get("matched_id")
        return "hit", ({"asset_id": str(matched)} if matched else {})
    if outcome == "miss":
        return "miss", {}
    if outcome == "error_failed_open":
        # Same reasoning as ``_guard``: the gate errored and let the question through, and the
        # record counts it. ``disabled`` below is a configuration; this is a failure.
        return "error", {"gate": "error_failed_open"}
    return "ok", {"gate": str(outcome or "unknown")}


def _rewrite(update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    rewrite = update.get("rewrite")
    if not isinstance(rewrite, Mapping):
        # Turn 1 writes `None` — the node deliberately did not run.
        return "ok", {"rewritten": False}
    outcome = rewrite.get("outcome")
    if outcome == "error":
        # `rewritten: True` was derived as `outcome != "unchanged"`, so a *failed* rewrite
        # reported as a successful one. The record separates the two deliberately —
        # `register/record.py` notes that collapsing them makes "any rate built on it read 0.0
        # on a run where every rewrite failed".
        return "error", {"rewritten": False}
    return "ok", {"rewritten": outcome != "unchanged"}


def _facet(update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """``ok`` with a hit count, or ``error`` when a declared channel did not run.

    **"0 hits" and "the channel never ran" are different facts and the row must not merge
    them.** A facet with no index returns ``hits: []`` and a ``channels`` map whose declared
    entries read ``failed`` — and the whole reason ``_channels_for`` exists is that its
    predecessor reported the *configuration* instead of the observation, so a facet that never
    consulted anything claimed it had. Emitting ``ok, n_hits: 0`` here would reintroduce exactly
    that: an operator reading "Terms: 0 hits" concludes the corpus has nothing to say, when what
    happened is that the channel was not wired up.

    ``failed`` is read off the facet's own returned ``channels`` rather than recomputed from the
    declaration table, for the same reason: the observation decides.
    """
    facets = update.get("facets")
    if not isinstance(facets, Mapping):
        return "ok", {}
    for result in facets.values():
        if not isinstance(result, Mapping):
            continue
        detail: dict[str, Any] = {"n_hits": len(result.get("hits") or ())}
        channels = result.get("channels")
        if isinstance(channels, Mapping):
            failed = sorted(k for k, v in channels.items() if v == "failed")
            if failed:
                detail["failed_channels"] = failed
                return "error", detail
        return "ok", detail
    return "ok", {}


def _route(update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    detail: dict[str, Any] = {}
    schemas = update.get("schemas")
    if isinstance(schemas, (list, tuple)):
        detail["schemas"] = [str(s) for s in schemas]
    retrieved = update.get("retrieved")
    if isinstance(retrieved, Mapping):
        ranking = retrieved.get("schema_ranking")
        if isinstance(ranking, (list, tuple)):
            detail["n_candidates"] = len(ranking)
    return "ok", detail


def _resolve(update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    detail: dict[str, Any] = {}
    retrieved = update.get("retrieved")
    if isinstance(retrieved, Mapping):
        pulled_in = retrieved.get("pulled_in")
        if isinstance(pulled_in, Mapping):
            detail["n_pulled_in"] = len(pulled_in)
    licensed = update.get("licensed")
    if isinstance(licensed, (list, tuple)):
        detail["n_licensed"] = len(licensed)
    return "ok", detail


def _connect(update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    crossings = update.get("crossings")
    detail: dict[str, Any] = {}
    if isinstance(crossings, (list, tuple)):
        detail["n_crossings"] = len(crossings)
    licensed = update.get("licensed")
    if isinstance(licensed, (list, tuple)):
        detail["n_licensed"] = len(licensed)
    return "ok", detail


def _assemble(update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """``n_chars`` of rendered context. **Not ``n_assets``, and that is the correction.**

    ADR 0010's table declared ``n_assets`` and the client rendered it, and nothing emitted it —
    review caught the contract and the code disagreeing. The fix is to drop it from the
    contract, not to produce it: ``assemble`` returns only ``delivery``, which carries the block
    and its hashes and no count, so the only way to emit one would be to read ``licensed`` off
    the state. That would make this the one reader that consults something other than what the
    node returned, for one cosmetic number — and "status and detail are observed from the
    update" is the rule that keeps every other row trustworthy. An absent fact stays absent.
    """
    delivery = update.get("delivery")
    if not isinstance(delivery, Mapping):
        return "ok", {}
    detail: dict[str, Any] = {}
    block = delivery.get("context_block")
    if isinstance(block, str):
        detail["n_chars"] = len(block)
    return "ok", detail


def _agent_core(update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    execution = update.get("execution")
    if not isinstance(execution, Mapping):
        return "ok", {}
    attempts = execution.get("attempts")
    return "ok", ({"n_attempts": len(attempts)} if isinstance(attempts, (list, tuple)) else {})


#: Per-stage detail readers. Absent stage → ``("ok", {})``, which is the right answer for a
#: node whose only interesting outcome is *that it finished*.
_DETAIL_BY_STAGE: dict[str, Any] = {
    "accept": _accept,
    "guard": _guard,
    "negative_gate": _negative,
    "rewrite": _rewrite,
    "facet_schema": _facet,
    "facet_term": _facet,
    "facet_metric": _facet,
    "facet_entity": _facet,
    "facet_example": _facet,
    "route": _route,
    "resolve": _resolve,
    "connect": _connect,
    "assemble": _assemble,
    "agent_core": _agent_core,
}
