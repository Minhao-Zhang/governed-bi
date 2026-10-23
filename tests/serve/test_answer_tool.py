"""The answer tool ends a turn with its assumptions, and the server checks them (ADR 0018).

The checks live in ``serve/assumptions.py``; most tests fake them, the last runs them. This drives
``ScriptedChatModel`` through ``agent_core_node``, then the same narrate, finish and record
nodes the graph runs.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from governed_bi.corpus.analyst import for_analyst
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.nodes.agent_core import agent_core_node
from governed_bi.serve.scripted_model import ScriptedChatModel

_BASES = frozenset({"layer", "convention", "guess", "found_in_query"})

_CONVENTION = {
    "statement": "Every customer row is counted.",
    "about": "customers",
    "basis": "convention",
    "asset_id": None,
}

_LAYER = {
    "statement": "Closed businesses are included.",
    "about": "closed",
    "basis": "layer",
    "asset_id": "term.closed",
}


def _answer_call(text: str, assumptions: list[dict[str, Any]], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{
            "name": "answer",
            "args": {"answer_text": text, "assumptions": assumptions},
            "id": call_id,
            "type": "tool_call",
        }],
    )


def _query_call(call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{
            "name": "run_query",
            "args": {"sql": "SELECT 1"},
            "id": call_id,
            "type": "tool_call",
        }],
    )


def _assets() -> dict[str, Any]:
    table = TableAsset(
        id="sales.customers", schema="sales", physical_name="customers",
        summary="customers", body="Customers.", columns=("sales.customers.id",),
    )
    column = ColumnAsset(
        id="sales.customers.id", schema="sales", parent_table="customers",
        physical_name="id", summary="id", physical_type="INTEGER",
    )
    return {a.id: a for a in (table, column)}


class _Answering:
    """Returns one row. The test is about the answer tool, not the driver."""

    dialect = "postgres"

    def execute(self, sql: str, **_kwargs: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        return (["id"], [(1,)], False)


def _config(model: Any, turn_id: str) -> dict[str, Any]:
    assets = _assets()
    return {
        "configurable": {
            "thread_id": turn_id,
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
            "connector": _Answering(),
            "assets_by_id": assets,
            "corpus": for_analyst(list(assets.values())),
        },
    }


def _state(turn_id: str) -> dict[str, Any]:
    return {
        "question": "how many customers",
        "turn_id": turn_id,
        "turn_index": 1,
        "thread_id": turn_id,
        "run_id": "run-answer",
        "question_id": "q-answer",
        "db_id": "main",
        "attempt_id": "attempt-answer",
        "corpus_content_hash": "c",
        "prompt_set_hash": "p",
        "settings_resolved": {},
        "n_re_served": 0,
        "retrieved_tables": ["sales.customers"],
        "messages": [],
        "usage": [],
    }


def _settle(state: dict[str, Any], core: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Narrate, finish and the turn row, in the graph's order."""
    from governed_bi.api.graph_app import record_node
    from governed_bi.serve.nodes.finish import finish
    from governed_bi.serve.nodes.narrate import narrate_node

    merged = {**state, **core}
    merged.update(asyncio.run(narrate_node(merged, config)))
    merged.update(finish(merged))
    merged.update(record_node()(merged))
    return merged


def _run(model: Any, turn_id: str) -> dict[str, Any]:
    state = _state(turn_id)
    config = _config(model, turn_id)
    core = asyncio.run(agent_core_node(state, config))
    return _settle(state, core, config)


def _passthrough(claims: Any, **_kwargs: Any) -> list[dict[str, Any]]:
    return [
        {
            "statement": claim.statement,
            "about": claim.about,
            "basis": claim.basis,
            "asset_id": claim.asset_id,
        }
        for claim in claims
    ]


def _downgrade(claims: Any, **_kwargs: Any) -> list[dict[str, Any]]:
    stored = _passthrough(claims)
    for item in stored:
        if item["basis"] == "layer":
            item["basis"] = "guess"
    return stored


def _none_delivered(_attempts: Any) -> None:
    return None


def _no_findings(_statement: Any, **_kwargs: Any) -> list[dict[str, Any]]:
    return []


def _install(monkeypatch: Any, *, check: Any, found: Any = _no_findings, delivered: Any = _none_delivered) -> None:
    import governed_bi.serve.tools as tools

    monkeypatch.setattr(tools, "check_layer_claims", check)
    monkeypatch.setattr(tools, "found_in_query", found)
    monkeypatch.setattr(tools, "delivered_statement", delivered)


def _tool_texts(state: dict[str, Any]) -> list[str]:
    return [
        str(message.content)
        for message in state.get("messages") or []
        if isinstance(message, ToolMessage)
    ]


def test_an_answered_turn_records_assumptions_on_the_record_and_the_turn(monkeypatch: Any) -> None:
    """run_query, then answer. The list is on the record and on the turn row the client reads."""
    found = [{
        "statement": "The statement keeps one row per customer.",
        "about": None,
        "basis": "found_in_query",
        "asset_id": None,
    }]
    _install(monkeypatch, check=_passthrough, found=lambda *_a, **_k: list(found))
    model = ScriptedChatModel(responses=[
        _query_call("q1"),
        _answer_call("There is 1 customer.", [_CONVENTION], "a1"),
    ])
    out = _run(model, "turn-answered")
    record = out["answer"]["record"]["assumptions"]
    projected = out["turns"][0]["assumptions"]

    assert out["answer"]["outcome"] == "answered", out["answer"]["outcome"]
    assert record == projected
    assert [item["basis"] for item in record] == ["convention", "found_in_query"]
    assert {item["basis"] for item in record} <= _BASES
    assert out["answer"]["answer_text"] == "There is 1 customer."
    assert out["answer"]["record"]["answer_tool_called"] is True
    assert len(model.prompts_seen) == len(model.responses)


def test_the_checks_add_no_model_call(monkeypatch: Any) -> None:
    """Same scripted turns whether the checks return nothing or return entries."""
    script = [
        _query_call("q1"),
        _answer_call("There is 1 customer.", [_CONVENTION], "a1"),
    ]

    _install(monkeypatch, check=lambda *_a, **_k: [], found=_no_findings)
    empty = ScriptedChatModel(responses=list(script))
    _run(empty, "turn-empty-checks")
    assert len(empty.prompts_seen) == len(script)

    entries = [{
        "statement": "A filter the question did not name.",
        "about": None,
        "basis": "found_in_query",
        "asset_id": None,
    }]
    _install(
        monkeypatch,
        check=_passthrough,
        found=lambda *_a, **_k: list(entries),
    )
    filled = ScriptedChatModel(responses=list(script))
    out = _run(filled, "turn-filled-checks")
    assert len(filled.prompts_seen) == len(script)
    assert out["answer"]["record"]["assumptions"][-1]["basis"] == "found_in_query"


def test_a_downgraded_layer_claim_is_corrected_once_then_accepted(monkeypatch: Any) -> None:
    """The first call names the asset and stays in the loop. The second keeps the downgrade."""
    _install(monkeypatch, check=_downgrade)
    model = ScriptedChatModel(responses=[
        _answer_call("Closed ones count.", [_LAYER], "a1"),
        _answer_call("Closed ones count.", [_LAYER], "a2"),
    ])
    out = _run(model, "turn-downgrade")
    texts = _tool_texts(out)

    assert any("term.closed" in text and "Fix the basis or the asset id" in text for text in texts)
    assert texts[-1] == "Answer recorded."
    assert len(model.prompts_seen) == len(model.responses)
    stored = out["answer"]["record"]["assumptions"]
    assert stored[0]["basis"] == "guess"
    assert stored[0]["asset_id"] == "term.closed"
    assert out["answer"]["record"]["answer_tool_called"] is True


def test_prose_is_reminded_once_then_the_answer_is_accepted(monkeypatch: Any) -> None:
    """One reminder, then the tool call. The reminder is not kept in the thread's messages."""
    from governed_bi.serve.nodes.agent_core import ANSWER_REMINDER

    _install(monkeypatch, check=_passthrough)
    model = ScriptedChatModel(responses=[
        AIMessage(content="three customers"),
        _answer_call("There are three customers.", [_CONVENTION], "a1"),
    ])
    out = _run(model, "turn-reminded")

    assert out["answer"]["outcome"] == "no_sql"
    assert out["answer"]["answer_text"] == "There are three customers."
    assert out["answer"]["record"]["answer_tool_called"] is True
    assert ANSWER_REMINDER in model.prompt_text(1)
    assert all(ANSWER_REMINDER not in str(m.content) for m in out["messages"])
    assert len(model.prompts_seen) == 2


def test_prose_after_the_reminder_is_no_answer(monkeypatch: Any) -> None:
    """Still prose after the reminder: the turn records no answer, and the text is not adopted."""
    from governed_bi.serve.nodes.agent_core import NO_ANSWER_CALL

    _install(monkeypatch, check=_passthrough)
    model = ScriptedChatModel(responses=[AIMessage(content="three customers")])
    out = _run(model, "turn-prose")

    assert out["answer"]["outcome"] == "crashed"
    assert out["answer"]["error_type"] == NO_ANSWER_CALL
    assert out["answer"]["answer_text"] is None
    assert out["answer"]["record"]["answer_tool_called"] is False
    assert out["answer"]["record"]["assumptions"] == []
    assert len(model.prompts_seen) == 2


def test_a_refused_statement_ending_in_prose_is_not_reminded(monkeypatch: Any) -> None:
    """The ledger already says refused, so the prose gets no reminder and no second call."""
    _install(monkeypatch, check=_passthrough)
    refused = AIMessage(content="", tool_calls=[{
        "name": "run_query", "args": {"sql": "DROP TABLE sales.customers"},
        "id": "q1", "type": "tool_call",
    }])
    model = ScriptedChatModel(responses=[refused, AIMessage(content="I cannot do that.")])
    out = _run(model, "turn-refused")

    assert out["answer"]["outcome"] == "refused"
    assert len(model.prompts_seen) == 2


def test_a_turn_with_no_query_stores_no_found_in_query_entries(monkeypatch: Any) -> None:
    """No executed statement, so the server adds none of its own entries."""
    seen: dict[str, Any] = {}

    def delivered(attempts: Any) -> None:
        seen["attempts"] = len(list(attempts))
        return None

    def found(statement: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        seen["statement"] = statement
        return [] if statement is None else [{
            "statement": "should not be stored",
            "about": None,
            "basis": "found_in_query",
            "asset_id": None,
        }]

    _install(monkeypatch, check=_passthrough, found=found, delivered=delivered)
    model = ScriptedChatModel(responses=[
        _answer_call("There are customers.", [_CONVENTION], "a1"),
    ])
    out = _run(model, "turn-no-query")
    stored = out["answer"]["record"]["assumptions"]

    assert seen["attempts"] == 0
    assert seen["statement"] is None
    assert stored == [{**_CONVENTION}]
    assert not any(item["basis"] == "found_in_query" for item in stored)


def test_the_real_checks_find_the_models_limit_and_downgrade_a_fabricated_id() -> None:
    """No fakes: the server's own row limit is not reported, the model's LIMIT is."""
    query = AIMessage(
        content="",
        tool_calls=[{
            "name": "run_query",
            "args": {"sql": "SELECT id FROM sales.customers LIMIT 3"},
            "id": "q1",
            "type": "tool_call",
        }],
    )
    model = ScriptedChatModel(responses=[
        query,
        _answer_call("Three customers.", [_LAYER], "a1"),
        _answer_call("Three customers.", [_LAYER], "a2"),
    ])
    out = _run(model, "turn-real")
    stored = out["answer"]["record"]["assumptions"]

    assert any("answer rejected" in text and "term.closed" in text for text in _tool_texts(out))
    assert [item["basis"] for item in stored] == ["guess", "found_in_query"]
    assert "LIMIT" in stored[1]["statement"]
    assert len(model.prompts_seen) == 3
