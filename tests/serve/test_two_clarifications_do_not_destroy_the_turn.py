"""``clarification_requested`` is reduced, so two answers in one super-step commit.

An un-annotated channel raises ``InvalidUpdateError`` on a second write. On resume both
``ask_user`` calls write together; without the reducer ``agent_core`` discards both answers
and seals each call as not executed.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from langgraph.errors import InvalidUpdateError
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from governed_bi.serve.agent_state import (
    GovernedAgentState,
    any_fail_closed,
    merge_by_call,
)


def test_the_reducer_keeps_a_decline_whatever_order_the_writes_arrive_in() -> None:
    """``or``, not last-write: one decline wins, independent of which write arrived second."""
    assert any_fail_closed(True, False) is True
    assert any_fail_closed(False, True) is True
    assert any_fail_closed(False, False) is False
    # Absent is not a decline: an un-written channel must not end a turn.
    assert any_fail_closed(None, None) is False
    assert any_fail_closed(None, True) is True


def test_two_writers_in_one_super_step_commit_instead_of_aborting() -> None:
    """The crash, reproduced against an un-reduced twin and shown absent on the real channel.

    The control is the point: without it this asserts only that a graph runs, which it would
    do even if the channel silently went back to LastValue.
    """

    class Unreduced(TypedDict, total=False):
        """The same state with ``clarification_requested`` left unreduced."""

        clarification_requested: bool
        attempts_by_call: Annotated[dict[str, Any], merge_by_call]

    def build(state_type: Any):
        graph = StateGraph(state_type)
        graph.add_node("start", lambda s: {})
        for name in ("declines", "answers"):
            graph.add_node(
                name,
                # One declines, one does not — the shape two answered `ask_user` calls
                # produce when both resume into the same super-step. `attempts_by_call` is
                # the evidence: it is keyed per call, so a lost writer is a missing key.
                lambda s, n=name: {
                    "clarification_requested": n == "declines",
                    "attempts_by_call": {n: {"path": "ask_user"}},
                },
            )
            graph.add_edge("start", name)
            graph.add_edge(name, END)
        graph.add_edge(START, "start")
        return graph.compile()

    with pytest.raises(InvalidUpdateError, match="clarification_requested"):
        build(Unreduced).invoke({})

    out = build(GovernedAgentState).invoke({"messages": []})
    assert out["clarification_requested"] is True, (
        "the decline was lost. `agent_core` lifts this onto `ServeState` for "
        "`classify_outcome`, so losing it turns a declined clarification into an ordinary turn"
    )
    assert sorted(out["attempts_by_call"]) == ["answers", "declines"], (
        "both writers must have committed; if one is missing the super-step aborted and this "
        "test is asserting against a turn that did not happen"
    )
