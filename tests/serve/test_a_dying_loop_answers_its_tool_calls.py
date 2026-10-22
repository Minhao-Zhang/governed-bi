"""A dying loop must answer a pending tool call, or the next turn replays it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command

from governed_bi.corpus.analyst import analyst_corpus_from_keys
from governed_bi.govern.check import GovernanceUsageError
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve import graph as graph_module
from governed_bi.serve.nodes import agent_core
from governed_bi.serve.nodes.agent_core import agent_core_node
from governed_bi.serve.scripted_model import ScriptedChatModel


def _ask_user(*ids: str) -> AIMessage:
    """One assistant message asking ``ask_user`` once per id. Needs no database."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ask_user",
                "args": {"question": f"which year, take {n}?", "basis": "data_definition"},
                "id": call_id,
                "type": "tool_call",
            }
            for n, call_id in enumerate(ids, start=1)
        ],
    )


RUN_QUERY = AIMessage(
    content="",
    tool_calls=[
        {
            "name": "run_query",
            "args": {"sql": "SELECT COUNT(*) AS n FROM beer_factory.customers"},
            "id": "c1",
            "type": "tool_call",
        }
    ],
)


class _Stub:
    """A connector that answers without a database, as in ``test_agent_core_partial_ledger``."""

    dialect = "postgres"

    def execute(self, sql: str, **_: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        return (["n"], [(3,)], False)


def _state() -> dict[str, Any]:
    return {
        "question": "how many customers?",
        "thread_id": "t-strand",
        "turn_index": 1,
        "turn_id": "turn-strand",
        "run_id": "r",
        "question_id": "q",
        "db_id": "beer_factory",
        "attempt_id": "a",
        "corpus_content_hash": "c",
        "prompt_set_hash": "p",
        "settings_resolved": {},
        "n_re_served": 0,
        # Routing has to succeed or the turn refuses before ``agent_core`` runs at all.
        "channel_route_hits": [("channel_schema", "beer_factory", 1.0)],
        "messages": [],
        "usage": [],
        "clarifications": [],
        "retrieved_tables": ["beer_factory.customers"],
    }


def _config(model: Any) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": "t-strand",
            # No guard rules: these tests are about the message list, not about governance.
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
            "connector": _Stub(),
            "corpus": analyst_corpus_from_keys(allowed=["beer_factory.customers.id"]),
        }
    }


def _dangling(messages: Any) -> dict[str, str]:
    """``{tool_call_id: tool name}`` for every ``tool_use`` nothing answered."""
    asked = {
        str(call["id"]): str(call.get("name") or "")
        for message in messages or ()
        if isinstance(message, AIMessage)
        for call in message.tool_calls or ()
    }
    answered = {
        str(getattr(message, "tool_call_id", ""))
        for message in messages or ()
        if str(getattr(message, "type", "")) == "tool"
    }
    return {k: v for k, v in asked.items() if k not in answered}


def _turn(model: Any) -> dict[str, Any]:
    """The node on its own. Enough for the two paths that need no checkpointer."""
    return asyncio.run(agent_core_node(_state(), _config(model)))




class _SlowModel(ScriptedChatModel):
    """A model call slower than the wall, so the wall fires on the frame after it."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        time.sleep(0.4)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_a_timeout_between_the_ask_and_the_tool_answers_the_pending_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOVERNED_BI_AGENT_NODE_TIMEOUT_S", "0.05")
    out = _turn(_SlowModel(responses=[_ask_user("c1")]))

    assert out["path_kind"] == "crashed"
    assert out["failure"] == {"stage": "agent_core", "error_type": "TimeoutError"}
    messages = out["messages"]
    assert not _dangling(messages), (
        f"unanswered tool_use {sorted(_dangling(messages))} committed to the thread; every later "
        "turn replays this history, so the thread is dead rather than the turn"
    )
    reply = messages[-1]
    assert getattr(reply, "status", None) == "error", (
        "a reply without error status reads as a result the tool produced"
    )
    assert "TimeoutError" in str(reply.content), (
        f"the reply is replayed to a model and has to say what happened, got {reply.content!r}"
    )


def test_both_calls_of_one_stranded_batch_are_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two calls to the *same* tool in one message — the shape that cost thread ``01a01bb8``."""
    monkeypatch.setenv("GOVERNED_BI_AGENT_NODE_TIMEOUT_S", "0.05")
    out = _turn(_SlowModel(responses=[_ask_user("c1", "c2")]))

    assert out["path_kind"] == "crashed"
    assert not _dangling(out["messages"]), (
        f"unanswered tool_use {sorted(_dangling(out['messages']))}; a name-keyed filter strands "
        "the second call to a tool it has already seen"
    )




def test_a_wiring_failure_raised_out_of_a_tool_answers_the_pending_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GovernanceUsageError`` is the production instance, and it is re-raised on purpose."""
    from governed_bi.serve import fetch

    def _unwired(*_a: Any, **_k: Any) -> tuple[str, dict[str, Any]]:
        raise GovernanceUsageError("bounds were never wired up")

    monkeypatch.setattr(fetch, "run_query", _unwired)
    out = _turn(ScriptedChatModel(responses=[RUN_QUERY, AIMessage("three customers")]))

    assert out["path_kind"] == "crashed"
    assert out["failure"] == {"stage": "agent_core", "error_type": "GovernanceUsageError"}
    assert not _dangling(out["messages"]), (
        f"unanswered tool_use {sorted(_dangling(out['messages']))} after a wiring failure; the "
        "turn is meant to fail, the thread is not"
    )
    assert "GovernanceUsageError" in str(out["messages"][-1].content)


def _paused(graph: Any, model: Any) -> dict[str, Any]:
    paused = graph.invoke(_state(), _config(model))
    assert paused.get("__interrupt__"), "precondition: ask_user did not pause the turn"
    return paused





def test_a_paused_turn_never_reaches_the_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    """The invariant stated as a structural fact rather than as a comment."""
    calls: list[Any] = []
    original = agent_core._sealed

    def spy(fresh: Any, failure: Any, **kwargs: Any) -> Any:
        calls.append((fresh, failure, kwargs))
        return original(fresh, failure, **kwargs)

    monkeypatch.setattr(agent_core, "_sealed", spy)
    graph = graph_module.compile_graph()
    paused = _paused(graph, ScriptedChatModel(responses=[_ask_user("c1"), AIMessage("ok: 2020")]))

    assert calls == [], f"a pause reached the repair: {calls!r}"
    # And nothing was committed at all, which is why there is nothing here to repair: the
    # pending ``tool_use`` lives in the *nested* agent's checkpoint until the resume.
    assert (paused.get("messages") or []) == []


def test_a_resume_still_gets_its_answer() -> None:
    """The paired positive, without which every assertion above is satisfiable by a graph that
    stopped resuming at all: the answer becomes the ``ask_user`` reply, and not an error one."""
    graph = graph_module.compile_graph()
    model = ScriptedChatModel(responses=[_ask_user("c1"), AIMessage("ok: 2020")])
    _paused(graph, model)

    done = graph.invoke(Command(resume="2020"), _config(model))

    assert done["path_kind"] == "answered", f"failure={done.get('failure')!r}"
    replies = [m for m in done["messages"] if str(getattr(m, "type", "")) == "tool"]
    assert [str(m.content) for m in replies] == ["2020"], (
        f"the human's answer is what closes the call, got {[str(m.content) for m in replies]!r}"
    )
    assert [getattr(m, "status", None) for m in replies] == ["success"]
    assert [c.get("answer") for c in done.get("clarifications") or []] == ["2020"]


def test_a_pause_returned_as_a_frame_is_left_alone() -> None:
    """The configuration where the pause does *not* raise, measured."""
    out = _turn(ScriptedChatModel(responses=[_ask_user("c1"), AIMessage("ok: 2020")]))

    replies = [m for m in out["messages"] if str(getattr(m, "type", "")) == "tool"]
    assert replies == [], (
        f"a pending clarification was answered by the repair: {[str(m.content) for m in replies]!r}"
    )


def test_a_clean_turn_is_left_exactly_as_it_was() -> None:
    """The guard against sealing turns that need no sealing — a spurious error reply is a
    message the model reads and reasons from."""
    out = _turn(ScriptedChatModel(responses=[AIMessage("three customers")]))

    assert out["path_kind"] == "answered"
    assert [getattr(m, "type", None) for m in out["messages"]] == ["ai"]
