"""``clarification_requested`` is reduced, so two answers in one super-step commit.

**The channel this covers was the only one on ``GovernedAgentState`` without a reducer**, and
``keep_newest``'s docstring — three declarations above it in the same file — already records
what that costs, for ``result_table``: LangGraph backs an un-annotated channel with LastValue,
which raises ``InvalidUpdateError`` on a second write in one super-step.

Reaching it with ``ask_user`` takes one more step than reaching it with ``run_query``. The
``pending_clarification`` latch refuses a second question while one is outstanding, so two
calls in one assistant message cannot both pause *on the same pass*. But ``interrupt()`` ends
the super-step, so on the pausing pass the second ``Send`` may never run at all — and a call
with no ``ToolMessage`` is exactly the one ``create_agent`` re-dispatches. Both re-run on
resume, the first returns from its interrupt and gives the latch back, the second then takes
it and pauses, and when that one is answered the two ``Command`` updates commit together.

What the crash produced is why this is not merely a robustness fix. ``agent_core``'s ``_run``
catches the error and returns ``path_kind="crashed"``, so **both human answers are discarded**
— ``clarifications`` comes back empty — and ``_sealed`` writes each of the two tool calls
*"Not executed: the turn ended before this tool ran"*. Two people-supplied answers, thrown
away, with a record saying nothing ran.

These tests drive the channel rather than the agent: reproducing the interrupt ordering needs
a model that emits two ``ask_user`` calls in one message and two resumes, which
``tests/serve/test_agent_tools_hitl.py`` already pays for on the single-question path. What is
unique here is the *merge*, and a two-writer super-step is the smallest thing that shows it.
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
    """``or``, not last-write. A fail-closed decline is a decision, not a candidate value.

    ``keep_newest`` would also stop the crash, and would be wrong here: its own docstring says
    "later" within a super-step is tool-call order, which is arbitrary. One caller declining is
    the whole meaning of the flag, so the answer does not depend on which arrived second.
    """
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
        """``clarification_requested`` as it was declared until 2026-09-18.

        ``attempts_by_call`` carries its real reducer here too, so the only difference
        between this and :class:`GovernedAgentState` is the channel under test.
        """

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
                # the witness: it is keyed per call, so a lost writer is a missing key.
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
