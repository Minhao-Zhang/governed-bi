"""Nested agent channels — where the turn's ledger survives a resume.

Tools write durable state via :class:`~langgraph.types.Command` into
:class:`GovernedAgentState` (checkpointed). Keyed by tool call id for idempotent
replay, exact cap counting, and attributable deliveries.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from langchain.agents import AgentState

from governed_bi.serve.ledger import INTROSPECTION_PATHS, attempt_field

__all__ = ["GovernedAgentState", "merge_by_call", "keep_newest", "AttemptBook"]


def merge_by_call(left: Any, right: Any) -> dict[str, Any]:
    """Merge two call-keyed maps; right wins per key."""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


def keep_newest(left: Any, right: Any) -> Any:
    """Take the later write. Exists so a second write in one super-step cannot abort the turn.

    **What it fixes (audit §13.2).** ``result_table`` was the one channel on this class with no
    reducer, so LangGraph backed it with a LastValue channel, which raises ``InvalidUpdateError``
    on a second write in the same super-step. Every successful ``run_query`` writes it. Two or
    more ``run_query`` calls in one assistant message therefore ran **every** statement against
    the database and *then* aborted the nested agent — measured on this tree: three parallel
    calls gave ``path_kind='crashed'``, three statements executed, and **zero** ledger rows,
    because the abort discarded the ``attempts_by_call`` writes from the same step.

    This is an audit-trail fix, not a feature. Across super-steps it is what LastValue already
    did — the repair loop's second successful query still replaces the first, which is the
    behaviour ``narrate`` and ``stamp`` depend on.

    **It does not choose.** Within one super-step "later" is tool-call order, which is arbitrary
    with respect to which candidate is *right*. A k>1 candidate design (§16.3③) must not lean on
    this: it needs a channel keyed by ``tool_call_id``, like the three above it, plus a real
    selection step. What this reducer buys that design is that the three executions are now
    ledgered instead of erased.
    """
    return right if right is not None else left


class GovernedAgentState(AgentState):
    """``AgentState`` plus channels a governed turn must not lose on resume."""

    #: ``tool_call_id -> AttemptRecord``. The governed-statement ledger (ADR 0006 §5).
    attempts_by_call: Annotated[dict[str, Any], merge_by_call]

    #: ``tool_call_id -> sha256(payload)[:16]``. What the tools handed the model.
    tool_delivered: Annotated[dict[str, str], merge_by_call]

    #: ``tool_call_id -> clarification``. One row per answered ``ask_user``.
    clarifications_by_call: Annotated[dict[str, Any], merge_by_call]

    #: ``tool_call_id -> plain-language assumption text``. One row per
    #: ``state_assumption`` call (Gap 1, utku-ai-deployment-targets.md) — the
    #: model's own self-reported "here is what I assumed answering this",
    #: distinct from ``clarifications_by_call`` (a question the model asked and
    #: a human answered) and from anything derivable from ``pulled_in``/
    #: ``licensed`` (which say a definition was *available*, not that it was
    #: *applied*).
    assumptions_by_call: Annotated[dict[str, Any], merge_by_call]

    #: Last successful query's result table. Reduced, not LastValue: see :func:`keep_newest`.
    result_table: Annotated[dict[str, Any] | None, keep_newest]


class AttemptBook:
    """Attempt cap over committed ∪ in-flight *answering* tool call ids.

    Committed alone misses parallel siblings in one super-step; in-flight alone
    resets on resume. ``refund`` releases a slot when an admitted call produces no row.
    """

    def __init__(self, cap: int) -> None:
        self.cap = int(cap)
        self._in_flight: set[str] = set()
        #: One ledger row for "the cap ended this turn", not one per post-cap call.
        self.cap_recorded = False

    def charged(self, committed: Mapping[str, Any] | None) -> int:
        # ``committed`` is the turn's whole ``attempts_by_call`` ledger, and that ledger
        # also carries ``sample_rows`` introspection rows (audit visibility, not an
        # answering statement — see ``ledger.answering_attempts``). Counting those against
        # this cap meant a model that checked which of two similarly-named columns was the
        # right join key before writing SQL could exhaust the cap on introspection alone,
        # leaving zero attempts for the first real ``run_query``. ``_in_flight`` needs no
        # equivalent filter: only ``run_query`` ever adds to it.
        answering_ids = {
            call_id
            for call_id, attempt in (committed or {}).items()
            if attempt_field(attempt, "path") not in INTROSPECTION_PATHS
        }
        return len(answering_ids | self._in_flight)

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
