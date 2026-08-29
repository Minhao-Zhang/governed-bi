"""Live stage-event stream — wire shape for ADR 0010.

Status is observed from node updates, never declared. Stream retention follows
ADR 0006 §11. :func:`emit` never raises.

``kind`` / ``step`` / ``id`` restore an identity the channel throws away: ``stream_mode="custom"``
is the one mode LangGraph strips node identity from — building a chunk it drops the last
checkpoint-namespace segment, which is ``node:task_id`` (``CHECKPOINT_NS.split(NS_SEP)[:-1]`` in
``pregel/main.py``, 1.2.10) — and it is the only channel a node may write to.

The rest is domain: ``rail_observation`` computes ``blocked`` / ``refused`` / ``declined`` /
``hit`` / ``miss`` from a node's update, and ``error_type`` records an exception's **class**
where the native ``tasks`` payload carries ``repr(exc)``, which ADR 0006 §11 keeps off the wire.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import Any

# Module-scope import: function-level import trips blockbuster's ``os.getcwd``.
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

#: Monotonic within this process. Client owns ordering across SSE connections.
#: ``itertools.count`` is safe under concurrent facet fan-out.
_SEQ = itertools.count(1)

#: Stage that carries ``serve_path`` on its ``start``.
FIRST_STAGE = "accept"

#: Stages that always run (never silenced by a terminal ``path_kind``).
#: ``accept`` clears ``path_kind`` and would otherwise be silenced by a prior turn's terminal.
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
    """Write one event to the ``custom`` stream. Never raises."""
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
    except Exception:  # noqa: BLE001 — an observability channel must not fail a turn
        return


def rail_event_id(stage: str, state: Mapping[str, Any]) -> str:
    """Stable rail row id across resume replay (keyed on ``turn_id``)."""
    return f"{stage}:{state.get('turn_id') or 'accept'}"


def tool_event_id(stage: str, call_id: str) -> str:
    """Tool row id keyed on ``tool_call_id``."""
    return f"{stage}:{call_id}"


def silenced_by_terminal_state(stage: str, state: Mapping[str, Any]) -> bool:
    """True when the node will no-op because the turn already ended."""
    if stage in TERMINAL_HANDLERS:
        return False
    return state.get("path_kind") in _TERMINAL_PATH_KINDS


def rail_observation(stage: str, update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """``(status, detail)`` for a rail, observed from the update it returned."""
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
        # A declining ``route`` or ``connect`` keeps the numbers it got as far as computing,
        # so the row that most needs explaining is not the least informative one.
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
    """Refusal/decline reason under both ``terminal_reason`` and ``reason``."""
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
        return "error", {"gate": "error_failed_open"}
    if outcome == "blocked":
        detail: dict[str, Any] = {}
        # rule_id is closed-vocabulary; GuardVerdict.detail is free text and dropped.
        if guard.get("rule_id"):
            detail["rule_id"] = str(guard["rule_id"])
        return "blocked", detail
    return "ok", {}


def _negative(update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """``hit`` / ``miss`` / ``ok`` (disabled gate reports ``ok``, not ``miss``)."""
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
        return "error", {"gate": "error_failed_open"}
    return "ok", {"gate": str(outcome or "unknown")}


def _facet(update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """``ok`` with hit count, or ``error`` when a declared channel failed."""
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
    """``n_chars`` of rendered context (observed from the delivery update)."""
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


def _narrate(update: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """``source``: skipped / none / narrated — observed from whether ``answer_text`` is present."""
    if "answer_text" not in update:
        return "ok", {"source": "skipped"}
    text = update.get("answer_text")
    if not text:
        return "ok", {"source": "none"}
    return "ok", {"source": "narrated", "n_chars": len(str(text))}


#: Per-stage detail readers. Absent stage → ``("ok", {})``.
_DETAIL_BY_STAGE: dict[str, Any] = {
    "accept": _accept,
    "guard": _guard,
    "negative_gate": _negative,
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
    "narrate": _narrate,
}
