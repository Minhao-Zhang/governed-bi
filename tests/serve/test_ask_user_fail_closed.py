"""Fail-closed decline / ranking cancel, and deferred proceed."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import Command

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.graph import compile_graph
from governed_bi.serve.resume import CALLER_KEY
from governed_bi.serve.scripted_model import ScriptedChatModel

ASKED = "analyst-7"


def _ask(basis: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ask_user",
                "args": {"question": "which year?", "basis": basis},
                "id": "c1",
                "type": "tool_call",
            }
        ],
    )


def _run_query() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "run_query",
                "args": {"sql": "SELECT id FROM sales.customers"},
                "id": "c2",
                "type": "tool_call",
            }
        ],
    )


def _config(thread_id: str, model: Any) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": thread_id,
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
            CALLER_KEY: ASKED,
        }
    }


def _turn(thread_id: str) -> dict[str, Any]:
    return {
        "question": "revenue?",
        "thread_id": thread_id,
        "turn_index": 1,
        "turn_id": f"turn-{thread_id}",
        "run_id": "r",
        "question_id": "q",
        "db_id": "sales",
        "attempt_id": "a",
        "corpus_content_hash": "c",
        "prompt_set_hash": "p",
        "knobs_resolved": {},
        "n_re_served": 0,
        "facet_route_hits": [("facet_schema", "sales", 1.0)],
        "messages": [],
        "usage": [],
        "identity": {"token": ASKED, "id": ASKED},
        "clarifications": [],
    }


def _payload(paused: Mapping[str, Any]) -> dict[str, Any]:
    interrupts = paused.get("__interrupt__") or ()
    item = interrupts[0]
    value = getattr(item, "value", item)
    assert isinstance(value, dict)
    return value


def test_decline_stamps_clarification_and_does_not_run_query() -> None:
    model = ScriptedChatModel(responses=[_ask("data_definition"), _run_query(), AIMessage("guess")])
    graph = compile_graph()
    config = _config("t-decline", model)
    paused = graph.invoke(_turn("t-decline"), config)
    assert paused.get("__interrupt__")
    payload = _payload(paused)
    assert payload["basis"] == "data_definition"

    done = graph.invoke(Command(resume={"declined": True}), config)
    assert done["answer"]["outcome"] == "clarification"
    names = [
        tc.get("name")
        for m in (done.get("messages") or [])
        for tc in (getattr(m, "tool_calls", None) or [])
    ]
    assert "run_query" not in names
    row = (done.get("clarifications") or [None])[0]
    assert row and row["resolution"] == "declined"
    assert row["deferred"] is False


def test_ranking_cancel_is_fail_closed_like_decline() -> None:
    model = ScriptedChatModel(responses=[_ask("ranking_ambiguity"), _run_query(), AIMessage("guess")])
    graph = compile_graph()
    config = _config("t-cancel", model)
    paused = graph.invoke(_turn("t-cancel"), config)
    assert _payload(paused)["basis"] == "ranking_ambiguity"
    done = graph.invoke(Command(resume={"cancelled": True}), config)
    assert done["answer"]["outcome"] == "clarification"
    assert (done.get("clarifications") or [{}])[0].get("resolution") == "declined"
    assert not done.get("__interrupt__"), "ranking cancel must consume the interrupt"


def test_defer_proceeds_and_stamps_the_row() -> None:
    model = ScriptedChatModel(responses=[_ask("data_definition"), AIMessage("ok under the constraint")])
    graph = compile_graph()
    config = _config("t-defer", model)
    graph.invoke(_turn("t-defer"), config)
    done = graph.invoke(Command(resume={"deferred": True}), config)
    assert done["answer"]["outcome"] != "clarification" or done.get("clarifications")
    row = (done.get("clarifications") or [None])[0]
    assert row and row["deferred"] is True
    assert row["resolution"] == "deferred"
    assert "constraint" in (row.get("answer") or "").lower() or "deferred" in (row.get("answer") or "").lower()
