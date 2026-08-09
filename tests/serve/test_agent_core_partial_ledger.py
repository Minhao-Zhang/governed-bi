"""A crash in the agent loop must not erase the ledger of work that already happened.

**The defect, reproduced.** A model call that dies *after* a governed statement executed — which
is exactly what an exhausted retry looks like — left the node raising, ``wrap_node`` returning
``{failure, path_kind}`` and nothing else, and the audit record showing ``attempts: []`` for a
turn whose SQL had reached the database. ``usage`` and ``result_table`` went with it, so the
turn was also priced at zero.

The cause is structural rather than a bug: LangGraph's subgraph documentation says *"the parent
graph treats the entire subgraph execution as a single step"*, so an exception discards the whole
super-step's writes. Two candidate fixes were measured **not** to work — reading the nested state
back after the raise, and ``create_agent(checkpointer=True)`` — before ``agent.stream`` was.

For a system whose thesis is that the ledger *is* the artifact, "executed but unrecorded" is the
one direction it must not fail in. These tests hold that.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.nodes.agent_core import agent_core_node
from governed_bi.serve.scripted_model import ScriptedChatModel
from governed_bi.serve.wrap import wrap_node

ASK = AIMessage(
    "",
    tool_calls=[
        {
            "name": "run_query",
            "args": {"sql": "SELECT COUNT(*) AS n FROM beer_factory.customers"},
            "id": "c1",
        }
    ],
)


class _DiesAfterOneToolCall(ScriptedChatModel):
    """Answers once with a tool call, then raises — an exhausted provider retry's shape."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        if any(getattr(m, "type", "") == "tool" for m in messages):
            raise openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _run_turn(model: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "turn_index": 1,
        "turn_id": "t-partial",
        "messages": [],
        "usage": [],
    }
    config = {
        "configurable": {
            "thread_id": "t-partial",
            "policy": GovernancePolicy(),
            "agent_model": model,
        }
    }
    # Through `wrap_node`, because that is the boundary that used to be the whole story: it
    # turned the exception into `{failure, path_kind}` and the ledger died with the frame.
    return asyncio.run(wrap_node("agent_core", agent_core_node)(state, config))


def test_a_crash_after_a_governed_statement_keeps_the_attempt() -> None:
    """The turn crashed **and** one statement was governed. Both are recorded.

    `attempts: []` beside a populated `generated_sql` is the artifact contradicting itself, and
    it is what `serve/agent_state.py` was written to stop happening on a resume. A crash is the
    other way to reach it.
    """
    out = _run_turn(_DiesAfterOneToolCall(responses=[ASK]))

    assert out["path_kind"] == "crashed"
    assert out["failure"] == {"stage": "agent_core", "error_type": "APITimeoutError"}

    execution = out.get("execution") or {}
    attempts = execution.get("attempts") or []
    assert len(attempts) == 1, (
        "the statement reached governance before the model died; a record with no attempt says "
        f"the turn touched nothing, got {attempts!r}"
    )
    assert out.get("generated_sql"), "the statement is named, not just counted"


def test_a_crash_still_bills_the_tokens_it_spent() -> None:
    """The successful calls cost money whatever happened to the last one.

    `usage` was `None` on this path, so `measure/price.py` priced a turn that had really run a
    model at zero — the same shape as the missing guard and rewriter rows, reached by crashing
    instead of by never being written.
    """
    out = _run_turn(_DiesAfterOneToolCall(responses=[ASK]))
    assert [row.get("stage") for row in (out.get("usage") or [])] == ["agent_core"]


def test_the_messages_the_loop_did_produce_survive() -> None:
    """The AI turn that asked for the query and the tool result it got back.

    Without them the conversation loses a turn it really had, and the next turn's model sees a
    history that never happened.
    """
    out = _run_turn(_DiesAfterOneToolCall(responses=[ASK]))
    assert [getattr(m, "type", None) for m in (out.get("messages") or [])] == ["ai", "tool"]


def test_a_clean_turn_is_unchanged_and_carries_no_failure() -> None:
    """The guard against fixing the crash path by making every turn look crashed."""
    out = _run_turn(ScriptedChatModel(responses=[AIMessage("three customers")]))

    assert out["path_kind"] == "answered"
    assert "failure" not in out, "a turn that did not fail must not carry a failure marker"


def test_a_node_timeout_keeps_the_streamed_ledger() -> None:
    """``agent_core`` owns its hang-stop so a TimeoutError still projects the ledger.

    The bound lives inside the node and fires between astream frames, so a timeout
    still sees the last committed values snapshot (including attempts). A slow *next*
    model call after the tool is long enough past the deadline that the frame completes
    and then the hang-stop stamps crashed — mid-call cancel is deliberately not used
    (see F1 / ``wrap.py``'s to_thread hazard).
    """
    import os
    import time

    from langchain_core.outputs import ChatGeneration, ChatResult

    class _HangsAfterOneToolCall(_DiesAfterOneToolCall):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
            if any(getattr(m, "type", "") == "tool" for m in messages):
                time.sleep(0.5)
                return ChatResult(
                    generations=[ChatGeneration(message=AIMessage("too late"))]
                )
            return ScriptedChatModel._generate(
                self, messages, stop=stop, run_manager=run_manager, **kwargs
            )

    os.environ["GOVERNED_BI_AGENT_NODE_TIMEOUT_S"] = "0.15"
    try:
        out = _run_turn(_HangsAfterOneToolCall(responses=[ASK]))
    finally:
        del os.environ["GOVERNED_BI_AGENT_NODE_TIMEOUT_S"]

    assert out["path_kind"] == "crashed"
    assert out["failure"] == {"stage": "agent_core", "error_type": "TimeoutError"}
    attempts = (out.get("execution") or {}).get("attempts") or []
    assert len(attempts) == 1, (
        "the statement ran before the hang; a timeout must not drop it, got "
        f"{attempts!r}"
    )
    assert out.get("generated_sql")


def test_a_timeout_overlapping_in_flight_run_query_still_records_the_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hang-stop must not cancel mid-``to_thread`` after the statement finished.

    ``asyncio.wait_for`` around ``_run`` used to cancel the tool coroutine between DB
    completion and the ``attempts_by_call`` write, so the projected ledger omitted a
    statement that had already run. Deadline checks between frames let the tool finish
    recording first.
    """
    import os
    import time

    from governed_bi.serve import fetch

    def _slow_run_query(*_a: Any, **_k: Any) -> tuple[str, dict[str, Any]]:
        time.sleep(0.35)
        return (
            '{"columns":["n"],"rows":[[1]],"row_count":1,"truncated":false}',
            {
                "verdict_layer": None,
                "passed": True,
                "reason_code": "passed",
                "path": "agent",
                "executed_sql": "SELECT 1 AS n",
            },
        )

    monkeypatch.setattr(fetch, "run_query", _slow_run_query)
    os.environ["GOVERNED_BI_AGENT_NODE_TIMEOUT_S"] = "0.05"
    try:
        out = _run_turn(
            ScriptedChatModel(
                responses=[
                    ASK,
                    AIMessage("one row"),
                ]
            )
        )
    finally:
        del os.environ["GOVERNED_BI_AGENT_NODE_TIMEOUT_S"]

    assert out["path_kind"] == "crashed"
    assert out["failure"] == {"stage": "agent_core", "error_type": "TimeoutError"}
    attempts = (out.get("execution") or {}).get("attempts") or []
    assert len(attempts) == 1, (
        "run_query finished under a firing node timeout; omitting it is executed-but-"
        f"unrecorded, got {attempts!r}"
    )
    assert attempts[0].get("executed_sql") == "SELECT 1 AS n"
    assert out.get("generated_sql") == "SELECT 1 AS n"


def test_two_statements_in_one_super_step_keep_both_ledger_rows() -> None:
    """Parallel tool calls must not abort the step that already ran their SQL (audit §13.2).

    ``result_table`` was the one channel on ``GovernedAgentState`` declared without a reducer,
    so LangGraph backed it with LastValue, which raises ``InvalidUpdateError`` on a second write
    in one super-step — and every successful ``run_query`` writes it. Measured before the fix:
    three parallel calls executed three statements and recorded **zero** attempts, because the
    abort discarded the ``attempts_by_call`` writes from the same step. "Executed but
    unrecorded" is the one direction this file exists to prevent.

    Written against the channel rather than a live turn on purpose: the defect is the channel's
    declaration, and reproducing it end to end needs a Postgres server the CI box does not have.
    """
    from langgraph.graph import START, StateGraph

    from governed_bi.serve.agent_state import GovernedAgentState

    def writer(call_id: str) -> Any:
        def node(_state: dict) -> dict:
            return {
                "result_table": {"columns": ["n"], "rows": [[call_id]], "row_count": 1},
                "attempts_by_call": {call_id: {"executed_sql": f"SELECT '{call_id}'"}},
            }

        return node

    graph = StateGraph(GovernedAgentState)
    graph.add_node("first", writer("c1"))
    graph.add_node("second", writer("c2"))
    graph.add_edge(START, "first")
    graph.add_edge(START, "second")

    out = graph.compile().invoke({"messages": []})

    assert set(out["attempts_by_call"]) == {"c1", "c2"}, (
        "both statements reached governance in the same step; a ledger missing one says the "
        f"turn touched less than it did, got {sorted(out.get('attempts_by_call') or {})}"
    )
    # Deliberately not asserting *which* table survives. The reducer takes the later write, and
    # "later" is tool-call order, which says nothing about which candidate is right. Choosing is
    # a selection step that does not exist yet (§16.3③); this only stops the crash.
    assert out["result_table"] is not None
