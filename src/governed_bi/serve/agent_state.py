"""Checkpointed agent channels so a resumed turn does not get a fresh attempt budget.

``create_agent`` skips a call that already has a ``ToolMessage``, but the outer node
body re-runs and rebuilds :class:`AttemptBook` empty. The count has to live here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from langchain.agents import AgentState

from governed_bi.registry.stages import ATTEMPT_CAP_REFUSED_BY
from governed_bi.serve.ledger import INTROSPECTION_PATHS, attempt_field

__all__ = [
    "GovernedAgentState",
    "merge_by_call",
    "keep_newest",
    "any_fail_closed",
    "AttemptBook",
    "CAP_LEDGER_KEY",
]

# One shared key, not ``f"cap:{call_id}"``: the book is rebuilt per ``build_tools``
# call and two writers exist, so only a constant key collapses to one cap row.
CAP_LEDGER_KEY = "cap"


def merge_by_call(left: Any, right: Any) -> dict[str, Any]:
    merged = dict(left or {})
    merged.update(right or {})
    return merged


def keep_newest(left: Any, right: Any) -> Any:
    """Later write wins, so a second write in one super-step does not abort the turn.

    A LastValue channel raises ``InvalidUpdateError`` on that second write. Parallel
    ``run_query`` calls then discarded the ledger. Within a step, "later" is call order.
    """
    return right if right is not None else left


def any_fail_closed(left: Any, right: Any) -> bool:
    """``True`` if either write ends the turn. Order must not matter.

    A LastValue channel on ``clarification_requested`` discarded both answers when two
    ``ask_user`` calls landed in one step. A decline ends the turn whichever write arrives.
    """
    return bool(left) or bool(right)


class GovernedAgentState(AgentState):
    # Governed-statement ledger, keyed by tool call id (ADR 0006 §5).
    attempts_by_call: Annotated[dict[str, Any], merge_by_call]

    tool_delivered: Annotated[dict[str, str], merge_by_call]

    clarifications_by_call: Annotated[dict[str, Any], merge_by_call]

    # Reduced, not LastValue: see :func:`keep_newest`.
    result_table: Annotated[dict[str, Any] | None, keep_newest]

    # A fail-closed decline. :func:`any_fail_closed`, not last-write.
    clarification_requested: Annotated[bool, any_fail_closed]

    # The answer tool's writes. LastValue would abort the turn on a second call.
    answer_text: Annotated[str | None, keep_newest]
    assumptions: Annotated[list[Any] | None, keep_newest]
    # Matches the turn id the middleware holds. A later turn's id does not.
    answer_accepted_epoch: Annotated[str | None, keep_newest]


def _chargeable(committed: Mapping[str, Any] | None) -> set[str]:
    """``run_query`` rows only. ``sample_rows`` and the cap row do not spend a slot.

    Selected by ``path``, not key spelling. A row with no ``path`` counts, so the cap
    cannot under-count. The middleware counts the same population.
    """
    return {
        str(key)
        for key, row in (committed or {}).items()
        if attempt_field(row, "path") not in INTROSPECTION_PATHS
        and attempt_field(row, "reason_code") != ATTEMPT_CAP_REFUSED_BY
    }


class AttemptBook:
    """Cap over committed and in-flight ``run_query`` ids. It does not end the turn.

    Committed misses parallel siblings; in-flight resets on resume. The middleware ends
    the turn and never refunds; this book refunds a crash and writes the capped row.
    """

    def __init__(self, cap: int) -> None:
        self.cap = int(cap)
        self._in_flight: set[str] = set()
        # The book is rebuilt per ``build_tools`` call, so the ledger row uses
        # CAP_LEDGER_KEY. This flag only dedups the stream event.
        self.cap_recorded = False

    def charged(self, committed: Mapping[str, Any] | None) -> int:
        return len(_chargeable(committed) | self._in_flight)

    def admit(self, committed: Mapping[str, Any] | None, call_id: str) -> bool:
        if call_id and call_id in (_chargeable(committed) | self._in_flight):
            return True
        if self.charged(committed) >= self.cap:
            return False
        if call_id:
            self._in_flight.add(call_id)
        return True

    def refund(self, call_id: str) -> None:
        self._in_flight.discard(call_id)
