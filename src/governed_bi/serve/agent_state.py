"""Nested agent channels — where the turn's ledger survives a resume.

Tools write durable state via :class:`~langgraph.types.Command` into
:class:`GovernedAgentState` (checkpointed). Keyed by tool call id for idempotent
replay, exact cap counting, and attributable deliveries.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from langchain.agents import AgentState

__all__ = ["GovernedAgentState", "merge_by_call", "AttemptBook"]


def merge_by_call(left: Any, right: Any) -> dict[str, Any]:
    """Merge two call-keyed maps; right wins per key."""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class GovernedAgentState(AgentState):
    """``AgentState`` plus channels a governed turn must not lose on resume."""

    #: ``tool_call_id -> AttemptRecord``. The governed-statement ledger (ADR 0006 §5).
    attempts_by_call: Annotated[dict[str, Any], merge_by_call]

    #: ``tool_call_id -> sha256(payload)[:16]``. What the tools handed the model.
    tool_delivered: Annotated[dict[str, str], merge_by_call]

    #: ``tool_call_id -> clarification``. One row per answered ``ask_user``.
    clarifications_by_call: Annotated[dict[str, Any], merge_by_call]

    #: Last successful query's result table. Last write wins.
    result_table: dict[str, Any] | None


class AttemptBook:
    """Attempt cap over committed ∪ in-flight tool call ids.

    Committed alone misses parallel siblings in one super-step; in-flight alone
    resets on resume. ``refund`` releases a slot when an admitted call produces no row.
    """

    def __init__(self, cap: int) -> None:
        self.cap = int(cap)
        self._in_flight: set[str] = set()
        #: One ledger row for "the cap ended this turn", not one per post-cap call.
        self.cap_recorded = False

    def charged(self, committed: Mapping[str, Any] | None) -> int:
        return len(set(committed or ()) | self._in_flight)

    def admit(self, committed: Mapping[str, Any] | None, call_id: str) -> bool:
        """Whether this call may run, charging a slot if so."""
        if call_id and call_id in (set(committed or ()) | self._in_flight):
            return True
        if self.charged(committed) >= self.cap:
            return False
        if call_id:
            self._in_flight.add(call_id)
        return True

    def refund(self, call_id: str) -> None:
        """Release a slot charged for a call that produced no ledger row."""
        self._in_flight.discard(call_id)
