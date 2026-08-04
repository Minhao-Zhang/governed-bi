"""The nested agent's own channels — where the turn's ledger survives a resume.

**The ledger used to live in Python closures, and a closure does not survive an
interrupt.** ``build_tools`` held ``attempts_box`` and ``clar_box``; ``agent_core`` read them
back off the tool objects through ``_governed_attempts_box`` attributes. That works for one
straight-through execution and fails for the one path HITL exists to serve:

``ask_user`` calls ``interrupt()``, which aborts the outer node **without committing its
update** — so nothing the node was going to write reaches the checkpoint. On resume the node
re-executes, ``build_tools`` constructs fresh boxes, and the nested agent restores its
*messages* from its own checkpoint rather than re-invoking the tools. The ToolMessages are
therefore all present while the boxes that recorded what those calls did are empty. The turn
then reports ``terminal: "no_sql"`` with ``attempts: []`` beside a populated
``generated_sql`` — one row of the artifact contradicting itself — and ``tool_delivered: {}``
beside a ``delivery_hash`` computed over nothing. The attempt cap resets with the box, so a
cap of 1 admitted a second governed statement.

So the ledger belongs in the agent's state, which **is** checkpointed: LangGraph propagates
the checkpointer through ``config`` to a graph invoked inside a node, under its own namespace.
Tools write to it by returning :class:`~langgraph.types.Command`, which is the framework's own
answer to "a tool needs to record something durable".

**Keyed by tool call id, not appended to a list.** Three properties fall out of that choice
and all three matter here:

* **Idempotent under replay.** A replayed tool call writes the same key, so a resume cannot
  double-count an attempt. An ``operator.add`` list would append a second copy.
* **Countable for the cap.** ``len(attempts_by_call)`` is the number of governed statements,
  exactly, with no dependence on how many times a node executed.
* **Attributable.** ``tool_delivered``'s keys were fresh ``uuid4()`` values, so a delivery
  digest could not be traced to the call that produced it. The tool call id can.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from langchain.agents import AgentState

__all__ = ["GovernedAgentState", "merge_by_call", "AttemptBook"]


def merge_by_call(left: Any, right: Any) -> dict[str, Any]:
    """Merge two call-keyed maps; right wins per key.

    Concurrent-safe within a super-step because tool call ids are distinct, and idempotent
    across one because a replayed call writes the key it wrote before.
    """
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class GovernedAgentState(AgentState):
    """``AgentState`` plus the three things a governed turn must not lose.

    Passed to ``create_agent(state_schema=...)``. Without it the tools' ``Command`` updates
    would name channels the agent does not declare, and LangGraph would drop them silently —
    which is the same class of defect as ``route_node``'s undeclared ``schema_ranking``.
    """

    #: ``tool_call_id -> AttemptRecord``. The governed-statement ledger (ADR 0006 §5).
    attempts_by_call: Annotated[dict[str, Any], merge_by_call]

    #: ``tool_call_id -> sha256(payload)[:16]``. What the tools actually handed the model.
    tool_delivered: Annotated[dict[str, str], merge_by_call]

    #: ``tool_call_id -> clarification``. One row per answered ``ask_user``.
    clarifications_by_call: Annotated[dict[str, Any], merge_by_call]

    #: The last successful query's result table. Declared here for the reason in this class's
    #: docstring and no other: ``run_query`` returns it in a ``Command`` update, and a channel the
    #: agent does not declare is **dropped silently**. Last write wins, matching the outer
    #: channel — a turn's answer is about its last successful query.
    result_table: dict[str, Any] | None


class AttemptBook:
    """The attempt cap, counted over committed **∪** in-flight tool call ids.

    Two sources because neither alone is right, and each one alone has already been wrong:

    * **Committed only** — ``len(runtime.state["attempts_by_call"])`` — is durable across a
      resume but blind to siblings. ``ToolNode`` executes every tool call of one AI message
      within a single super-step, so two parallel ``run_query`` calls both read a count of
      zero and both proceed: a cap of 1 admitting 2, which is the symptom this replaces.
    * **In-flight only** — the previous ``attempts_box`` — is tight within one node execution
      and starts empty on the next one, which is how a resume reset the cap.

    The union is exact in both directions. ``refund`` exists because an admitted call that
    raises before ``prepare`` produces no ledger row, and charging a slot for a statement
    that was never governed would make the cap tighter than the record it is counted from.
    """

    def __init__(self, cap: int) -> None:
        # No ``or 3``: the cap is a declared knob with a declared default, so an explicit
        # cap of 0 is a configuration and coercing it back would be the same
        # absence-becomes-a-value defect one layer down.
        self.cap = int(cap)
        self._in_flight: set[str] = set()
        #: Whether the one ledger row for "the cap ended this turn" has been written. One row,
        #: not one per post-cap call: the cap is a terminal state, and a row per call would
        #: inflate the attempt count with calls where nothing was attempted.
        self.cap_recorded = False

    def charged(self, committed: Mapping[str, Any] | None) -> int:
        return len(set(committed or ()) | self._in_flight)

    def admit(self, committed: Mapping[str, Any] | None, call_id: str) -> bool:
        """Whether this call may run, charging a slot if so."""
        if call_id and call_id in (set(committed or ()) | self._in_flight):
            # A replay of a call already counted. It may run again — that is what a replay
            # is — but it must not consume a second slot.
            return True
        if self.charged(committed) >= self.cap:
            return False
        if call_id:
            self._in_flight.add(call_id)
        return True

    def refund(self, call_id: str) -> None:
        """Release a slot charged for a call that produced no ledger row."""
        self._in_flight.discard(call_id)
