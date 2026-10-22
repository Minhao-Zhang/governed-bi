"""A checkpoint holds every turn, not only the newest.

``answer`` and the other record channels reset each turn. ``turns`` is appended by ``record``
after ``accept`` resets, on the served graph. The saver is supplied here because the server
graph compiles without one.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from governed_bi.api.graph_app import record_node
from governed_bi.corpus.schema import ColumnAsset, SchemaAsset, TableAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.accept import accept_node
from governed_bi.serve.graph import as_sync, build_graph
from governed_bi.serve.scripted_model import ScriptedChatModel
from governed_bi.serve.session import from_assets
from governed_bi.serve.state import ACCUMULATING, PER_TURN_RESET

FIRST = "how many orders are there"
SECOND = "and how many customers"


@pytest.fixture(autouse=True)
def _isolated():
    """``trust()`` is process-wide, so a leaked registration makes another test pass by accident."""
    from governed_bi.serve.runtime import trust

    trust()
    yield
    trust()


class _EchoConnector:
    dialect = "postgres"

    def execute(self, sql: str, max_rows: int | None = None) -> Any:
        return (["n"], [(1,)], False)


class _TurnLog:
    """Everything ``record_node`` asks of a turn log, in memory."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def append_turn(self, record: Any, **kwargs: Any) -> tuple[str | None, str | None]:
        self.rows.append({"record": dict(record), **kwargs})
        return record.get("turn_id"), None


class _BrokenLog:
    """A log that cannot be written — a read-only ``runs/`` or a full disk, in one line."""

    def append_turn(self, record: Any, **kwargs: Any) -> tuple[str | None, str | None]:
        raise OSError("no space left on device")


def _assets() -> list[Any]:
    return [
        SchemaAsset(id="sales", name="sales", summary="sales orders and customers"),
        TableAsset(
            id="sales.orders", schema="sales", physical_name="orders",
            summary="Orders placed by customers.", body="One row per order.",
            columns=("sales.orders.id",),
        ),
        ColumnAsset(
            id="sales.orders.id", schema="sales", parent_table="sales.orders",
            physical_name="id", summary="Primary key.",
        ),
    ]


def _served(turn_log: Any) -> Any:
    """The served graph plus a saver; ``build_serve_graph`` compiles without one."""
    from governed_bi.serve.runtime import trust

    session = from_assets(
        _assets(),
        connector=_EchoConnector(),
        policy=GovernancePolicy(guard_rules_enabled={}),
        db_id="sales",
        corpus_content_hash_="corpus-under-test",
        agent_model=ScriptedChatModel(responses=[AIMessage(content="there are some orders")]),
    )
    assert not session.fatal_problems, [str(p) for p in session.fatal_problems]
    trust(dict(session.configurable()["configurable"]))
    graph = build_graph(
        accept=accept_node(session), record=record_node()
    ).compile(checkpointer=InMemorySaver())
    return as_sync(graph)


def _as_read_back(value: Any) -> Any:
    """Round-trip through JSON so tuples and dates match what a reader of the checkpoint sees."""
    return json.loads(json.dumps(value, default=str))


def _ask(graph: Any, question: str, *, thread: str) -> dict[str, Any]:
    return graph.invoke(
        {"messages": [HumanMessage(content=question)]},
        {"configurable": {"thread_id": thread}},
    )


def _turns(graph: Any, *, thread: str) -> list[dict[str, Any]]:
    """The channel through ``get_state``, not through ``invoke``.

    ``output_schema=ServeOutput`` narrows ``invoke`` to ``messages`` / ``answer``, so ``turns``
    is deliberately *not* in the returned dict.
    """
    return list(graph.get_state({"configurable": {"thread_id": thread}}).values.get("turns") or [])


def test_two_turns_on_one_thread_both_survive_in_state() -> None:
    """Turn two's ``answer`` is only the latest turn, while ``turns`` still holds both."""
    log = _TurnLog()
    graph = _served(log)

    first = _ask(graph, FIRST, thread="t-keeps-every-turn")
    second = _ask(graph, SECOND, thread="t-keeps-every-turn")

    rows = _turns(graph, thread="t-keeps-every-turn")
    assert len(rows) == 2, f"turn one was erased by turn two: {rows}"
    assert [r["question"] for r in rows] == [FIRST, SECOND], "oldest first, as keep_turns appends"

    ids = [r["record"]["turn_id"] for r in rows]
    assert all(ids) and len(set(ids)) == 2, f"every row must be addressable by turn_id: {ids}"
    # `ACCUMULATING`'s standing requirement: one flat list, so a row that cannot say whose it is
    # gets attributed to whichever turn the reader was looking at.
    assert {r["record"]["thread_id"] for r in rows} == {"t-keeps-every-turn"}, rows
    assert len({r["record"]["question_id"] for r in rows}) == 2, rows

    # The reset did happen: the newest turn is the only one `answer` knows about.
    assert second["answer"]["record"]["turn_id"] == ids[1]
    assert first["answer"]["record"]["turn_id"] == ids[0]
    assert second["answer"]["record"]["turn_id"] != first["answer"]["record"]["turn_id"]


def test_a_failed_log_write_still_records_the_turn_in_state() -> None:
    """A failed log write still appends the turn; the log is not the only copy."""
    graph = _served(_BrokenLog())
    out = _ask(graph, FIRST, thread="t-broken-log")

    assert out["answer"]["record"]["turn_id"], "a log failure must not fail the turn"
    (row,) = _turns(graph, thread="t-broken-log")
    assert row["record"]["turn_id"] == out["answer"]["record"]["turn_id"]


def test_a_turn_with_no_record_appends_nothing() -> None:
    """A paused turn (``ask_user``) has no ``turn_id`` yet, and an unaddressable row is noise.

    Driven at the node rather than through the graph: reaching this state through a real turn
    means interrupting one, and the subject here is the guard clause, not the interrupt.
    """
    log = _TurnLog()
    assert record_node()({"question": FIRST, "answer": None}) == {}
    assert record_node()({"question": FIRST, "answer": {"record": {}}}) == {}
    assert log.rows == []


def test_turns_accumulates_and_is_never_reset() -> None:
    """``turns`` accumulates: a ``[]`` reset is a no-op under ``keep_turns``."""
    assert "turns" in ACCUMULATING
    assert "turns" not in PER_TURN_RESET
