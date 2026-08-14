"""Nested agent channels — where the turn's ledger survives a resume.

Tools write durable state via :class:`~langgraph.types.Command` into
:class:`GovernedAgentState` (checkpointed), keyed by tool call id for exact cap counting and
attributable deliveries.

The key is *not* for idempotent replay: measured on langgraph 1.2.10, ``create_agent``
dispatches one ``Send("tools", [call])`` per call and the pending set excludes any call that
already has a ``ToolMessage``, so a ``run_query`` completed before an ``ask_user`` pause is not
re-executed on resume. What it buys is that the **outer** ``agent_core_node`` body *does* re-run
on resume, rebuilding :class:`AttemptBook` with an empty in-memory set — the count has to come
from this checkpointed channel or a resumed turn silently gets a fresh budget.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from langchain.agents import AgentState

from governed_bi.register.stages import ATTEMPT_CAP_REFUSED_BY
from governed_bi.serve.ledger import INTROSPECTION_PATHS, attempt_field

__all__ = [
    "GovernedAgentState",
    "merge_by_call",
    "keep_newest",
    "AttemptBook",
    "CAP_LEDGER_KEY",
]

#: The one ``attempts_by_call`` key a cap row may ever use.
#:
#: A constant rather than ``f"cap:{call_id}"``, because the key **is** the deduplication:
#: ``AttemptBook.cap_recorded`` lives on a book rebuilt per ``build_tools`` call, and two
#: writers exist (see :class:`~governed_bi.serve.nodes.agent_core`), so a shared key lets
#: :func:`merge_by_call` collapse them into one "the cap ended this turn" row.
CAP_LEDGER_KEY = "cap"


def merge_by_call(left: Any, right: Any) -> dict[str, Any]:
    """Merge two call-keyed maps; right wins per key."""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


def keep_newest(left: Any, right: Any) -> Any:
    """Take the later write. Exists so a second write in one super-step cannot abort the turn.

    ``result_table`` had no reducer, so LangGraph backed it with a LastValue channel, which
    raises ``InvalidUpdateError`` on a second write in the same super-step. Every successful
    ``run_query`` writes it, so two parallel calls ran every statement against the database and
    *then* aborted the nested agent, discarding the ``attempts_by_call`` writes from the same
    step — measured: three parallel calls, three statements executed, zero ledger rows (audit
    §13.2). Across super-steps this is what LastValue already did.

    **It does not choose.** Within one super-step "later" is tool-call order, which is arbitrary
    with respect to which candidate is right. A k>1 candidate design (§16.3③) needs a channel
    keyed by ``tool_call_id`` plus a real selection step.
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


def _chargeable(committed: Mapping[str, Any] | None) -> set[str]:
    """The committed ledger keys that are ``run_query`` attempts, and only those.

    **``attempts_by_call`` is the turn's ledger, not the attempt cap's.** Charging a slot per
    key meant ``sample_rows`` rows and the cap row itself silently spent governed statements the
    knob promises. It also made the cap uncountable: ``ToolCallLimitMiddleware`` counts
    ``run_query`` calls and nothing else, so both enforcers have to read the same population
    before either can be described as "five attempts".

    Selected by the row's ``path`` rather than by how its key is spelled, because a key prefix is
    a convention any new executor path can forget. A row with no ``path`` counts, which is the
    safe direction: under-counting hands out attempts the cap was meant to withhold.
    """
    return {
        str(key)
        for key, row in (committed or {}).items()
        if attempt_field(row, "path") not in INTROSPECTION_PATHS
        and attempt_field(row, "reason_code") != ATTEMPT_CAP_REFUSED_BY
    }


class AttemptBook:
    """Attempt cap over committed ∪ in-flight ``run_query`` tool call ids.

    Committed alone misses parallel siblings in one super-step; in-flight alone
    resets on resume. ``refund`` releases a slot when an admitted call produces no row.

    **It does not end the turn** — ``ToolCallLimitMiddleware`` does (see
    :class:`~governed_bi.serve.nodes.agent_core`). This book stays for the two things the native
    counter has no notion of: refunding a slot charged for a call that crashed before reaching
    governance, and writing the ledger row that makes ``execution_from_attempts`` return
    ``terminal: "capped"``.

    Both count the same population, so the effective cap is the smaller, not the sum. Native
    counts a proposal in ``after_model``, one node *earlier* than the tool body, and never
    refunds, so on the agent path it always trips first and this book's refusal is reachable
    only for callers that build tools without the agent.
    """

    def __init__(self, cap: int) -> None:
        self.cap = int(cap)
        self._in_flight: set[str] = set()
        #: One ``cap`` stream event per turn, not one per post-cap call. The ledger row cannot
        #: use this flag — the book is rebuilt on every ``build_tools`` call — so it is
        #: deduplicated by :data:`CAP_LEDGER_KEY` instead.
        self.cap_recorded = False

    def charged(self, committed: Mapping[str, Any] | None) -> int:
        return len(_chargeable(committed) | self._in_flight)

    def admit(self, committed: Mapping[str, Any] | None, call_id: str) -> bool:
        """Whether this call may run, charging a slot if so."""
        if call_id and call_id in (_chargeable(committed) | self._in_flight):
            return True
        if self.charged(committed) >= self.cap:
            return False
        if call_id:
            self._in_flight.add(call_id)
        return True

    def refund(self, call_id: str) -> None:
        """Release a slot charged for a call that produced no ledger row."""
        self._in_flight.discard(call_id)
