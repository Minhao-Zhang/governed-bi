"""A turn paused on ``ask_user`` keeps the tokens it spent and the routing it did.

Before, a paused row was written from an empty record: no usage, no schemas, no hashes. In the
baseline that left 13% of questions out of the cost and out of every stage of the funnel.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from governed_bi.eval.harness import with_paused_agent
from governed_bi.eval.projection import project_turn
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.graph import compile_graph
from governed_bi.serve.scripted_model import ScriptedChatModel, answer_call

ASK = AIMessage(
    content="",
    tool_calls=[
        {
            "name": "ask_user",
            "args": {"question": "which year?", "basis": "data_definition"},
            "id": "c1",
            "type": "tool_call",
        }
    ],
    usage_metadata={"input_tokens": 1200, "output_tokens": 40, "total_tokens": 1240},
)


def _turn() -> dict[str, Any]:
    return {
        "question": "revenue?",
        "thread_id": "t-paused",
        "turn_index": 1,
        "turn_id": "turn-t-paused",
        "run_id": "r",
        "question_id": "q",
        "db_id": "sales",
        "attempt_id": "a",
        "corpus_content_hash": "c",
        "prompt_set_hash": "p",
        "settings_resolved": {},
        "n_re_served": 0,
        "channel_route_hits": [("channel_schema", "sales", 1.0)],
        "messages": [],
        "usage": [],
        "clarifications": [],
    }


def _paused_row() -> dict[str, Any]:
    model = ScriptedChatModel(responses=[ASK, answer_call("2020")])
    config = {
        "configurable": {
            "thread_id": "t-paused",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
        }
    }
    graph = compile_graph()
    result = graph.invoke(_turn(), config)
    assert result.get("__interrupt__"), "the scripted model did not pause the turn"
    state = with_paused_agent(graph, config, result, model=model)
    return project_turn(state, question={"question_id": "q", "db_id": "sales"}, config="paused")


def test_a_paused_row_bills_the_agent_call_that_asked() -> None:
    row = _paused_row()

    assert row["outcome"] == "clarification"
    agent = [u for u in row["usage"] if u.get("stage") == "agent_core"]
    assert len(agent) == 1, row["usage"]
    assert agent[0]["input_tokens"] == 1200
    assert agent[0]["output_tokens"] == 40


def test_a_paused_row_keeps_its_routing_and_identity() -> None:
    row = _paused_row()

    assert row["schemas"] == ["sales"]
    assert row["corpus_content_hash"] == "c"
    assert row["prompt_set_hash"] == "p"
