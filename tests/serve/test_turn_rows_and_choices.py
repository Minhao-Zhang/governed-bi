"""The turn row keeps the rows the model saw, and ``ask_user`` can carry choices.

The rows stay off ``answer.record``. Compaction drops them from archived turns, oldest first.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from governed_bi.api.graph_app import record_node
from governed_bi.corpus.analyst import for_analyst
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.graph import as_sync, build_graph
from governed_bi.serve.scripted_model import ScriptedChatModel, answer_call
from governed_bi.serve.state import keep_turns
from governed_bi.serve.tools import build_tools


class _ManyRows:
    """One hundred and fifty rows. The preview is the driver's first twenty."""

    dialect = "postgres"

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, sql: str, **_kwargs: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        self.calls += 1
        return (["n"], [(f"cell-{i}",) for i in range(150)], True)


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


def _turn(thread: str) -> dict[str, Any]:
    return {
        "question": "how many customers",
        "thread_id": thread,
        "turn_index": 1,
        "turn_id": f"turn-{thread}",
        "run_id": "r",
        "question_id": "q",
        "db_id": "sales",
        "attempt_id": "a",
        "corpus_content_hash": "c",
        "prompt_set_hash": "p",
        "settings_resolved": {},
        "n_re_served": 0,
        "retrieved_tables": ["sales.customers"],
        "channel_route_hits": [("channel_schema", "sales", 1.0)],
        "messages": [],
        "usage": [],
        "clarifications": [],
    }


def _config(thread: str, model: Any, connector: Any) -> dict[str, Any]:
    assets = _assets()
    return {
        "configurable": {
            "thread_id": thread,
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
            "connector": connector,
            "assets_by_id": assets,
            "corpus": for_analyst(list(assets.values())),
        }
    }


def _query_then_answer() -> ScriptedChatModel:
    return ScriptedChatModel(responses=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": "run_query",
                "args": {"sql": "SELECT id FROM sales.customers"},
                "id": "q1",
                "type": "tool_call",
            }],
        ),
        answer_call("There are 150 customers."),
    ])


def test_a_150_row_result_is_stored_as_its_first_20_and_reread_without_the_connector() -> None:
    """The checkpoint holds the preview. Reading the thread back does not run the query again."""
    thread = "t-rows"
    connector = _ManyRows()
    graph = as_sync(
        build_graph(record=record_node()).compile(checkpointer=InMemorySaver())
    )
    config = _config(thread, _query_then_answer(), connector)
    out = graph.invoke(_turn(thread), config)

    record = (out.get("answer") or {}).get("record") or {}
    assert "result_table" not in record
    assert "cell-0" not in str(record), "the record gained a result row"
    assert connector.calls == 1, connector.calls

    snapshot = graph.get_state({"configurable": {"thread_id": thread}})
    assert connector.calls == 1, "reading the thread state called the connector"
    rows = list(snapshot.values.get("turns") or [])
    assert len(rows) == 1, rows
    table = rows[0].get("result_table")
    assert table is not None, rows[0].keys()
    assert table["row_count"] == 150
    assert table["truncated"] is True
    assert table["rows"] == [[f"cell-{i}"] for i in range(20)]
    assert "cell-20" not in str(table["rows"])


def _entry(turn_id: str, cell: str) -> dict[str, Any]:
    return {
        "asked_at": "2026-08-18T22:39:06+00:00",
        "question": "how many",
        "answer_text": cell,
        "outcome": "answered",
        "record": {"turn_id": turn_id, "outcome": "answered"},
        "assumptions": [],
        "result_table": {
            "columns": ["n"],
            "rows": [[cell]],
            "row_count": 1,
            "truncated": False,
        },
    }


def test_recent_turns_keep_their_tables_and_older_ones_lose_them_oldest_first() -> None:
    """A reopened thread shows the last ten tables; the eleventh-newest turn loses its own."""
    from governed_bi.serve.state import PREVIEWS_RETAINED

    rows: list[dict[str, Any]] = []
    for n in range(PREVIEWS_RETAINED):
        rows = keep_turns(rows, [_entry(f"t-{n}", f"row {n}")])
    assert all("result_table" in row for row in rows)

    rows = keep_turns(rows, [_entry("t-new", "newest")])
    assert "result_table" not in rows[0]
    assert [row["result_table"]["rows"] for row in rows[1:3]] == [[["row 1"]], [["row 2"]]]
    assert rows[-1]["result_table"]["rows"] == [["newest"]]


def _ask(choices: list[str] | None = None) -> AIMessage:
    args: dict[str, Any] = {"question": "Which year do you mean?", "basis": "data_definition"}
    if choices is not None:
        args["choices"] = choices
    return AIMessage(
        content="",
        tool_calls=[{"name": "ask_user", "args": args, "id": "c1", "type": "tool_call"}],
    )


def _payload(paused: dict[str, Any]) -> dict[str, Any]:
    interrupts = paused.get("__interrupt__") or ()
    value = getattr(interrupts[0], "value", interrupts[0])
    assert isinstance(value, dict)
    return value


def _pause(thread: str, choices: list[str] | None = None) -> dict[str, Any]:
    graph = as_sync(build_graph().compile(checkpointer=InMemorySaver()))
    model = ScriptedChatModel(responses=[_ask(choices), answer_call("ok")])
    return graph.invoke(_turn(thread), _config(thread, model, _ManyRows()))


def test_two_choices_are_in_the_interrupt_and_none_leaves_the_key_off() -> None:
    with_choices = _payload(_pause("t-choices", ["calendar year", "fiscal year"]))
    assert with_choices["choices"] == ["calendar year", "fiscal year"]
    assert with_choices["kind"] == "clarification"

    freeform = _payload(_pause("t-freeform"))
    assert freeform["question"] == "Which year do you mean?"
    assert "choices" not in freeform


def _ask_tool() -> Any:
    assets = _assets()
    state = {
        "question": "what is revenue?",
        "turn_id": "turn-clar",
        "turn_index": 1,
        "retrieved_tables": ["sales.customers"],
        "messages": [],
        "settings_resolved": {},
    }
    config = {
        "configurable": {
            "thread_id": "t-clar",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "assets_by_id": assets,
            "corpus": for_analyst(list(assets.values())),
        }
    }
    return {tool.name: tool for tool in build_tools(state, config)}["ask_user"]


def _runtime() -> Any:
    from langchain.tools import ToolRuntime

    return ToolRuntime(
        state={"attempts_by_call": {}},
        context=None,
        config={"configurable": {}},
        stream_writer=lambda _chunk: None,
        tool_call_id="c-reject",
        store=None,
    )


def test_five_choices_and_a_schema_choice_are_rejected_before_any_pause() -> None:
    """``interrupt`` outside a graph raises, so a rejection is a tool reply and nothing else."""
    ask = _ask_tool()
    five = asyncio.run(ask.coroutine(
        question="Which year do you mean?",
        basis="data_definition",
        runtime=_runtime(),
        choices=["one", "two", "three", "four", "five"],
    ))
    reply = str(five.update["messages"][0].content)
    assert "ask_user rejected" in reply
    assert "four" in reply
    assert list(five.update) == ["messages"]

    leaked = asyncio.run(ask.coroutine(
        question="Which year do you mean?",
        basis="data_definition",
        runtime=_runtime(),
        choices=["calendar year", "schema.table.column"],
    ))
    leaked_reply = str(leaked.update["messages"][0].content)
    assert "ask_user rejected" in leaked_reply
    assert "schema.table.column" in leaked_reply
    assert list(leaked.update) == ["messages"]

    empty = asyncio.run(ask.coroutine(
        question="Which year do you mean?",
        basis="data_definition",
        runtime=_runtime(),
        choices=["calendar year", "  "],
    ))
    empty_reply = str(empty.update["messages"][0].content)
    assert "ask_user rejected" in empty_reply
    assert "empty" in empty_reply
    assert list(empty.update) == ["messages"]

    with pytest.raises(RuntimeError):
        asyncio.run(ask.coroutine(
            question="Which year do you mean?",
            basis="data_definition",
            runtime=_runtime(),
            choices=["calendar year", "fiscal year"],
        ))
