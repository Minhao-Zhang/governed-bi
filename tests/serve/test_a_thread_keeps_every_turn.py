"""``ServeState.turns``: a checkpoint holds the whole conversation's audit trail, not the newest turn.

**The defect this closes.** Every channel a turn's record is built from — ``answer``,
``execution``, ``generated_sql``, ``answer_text`` — is in ``PER_TURN_RESET``, so turn two erased
turn one and a thread's checkpoint could only ever describe its most recent question.
``runs/serve/*.jsonl`` was the only surviving copy, which made the audit surface depend on a
filesystem beside the process rather than on the store the conversation already lives in.

**Why the test drives the real served topology.** ``record`` is mounted on exactly one graph —
``build_graph(record=...)``, which only ``api/graph_app.build_serve_graph`` calls — and the reset
that this channel has to survive is written by ``accept``, the node in front of ``guard`` on that
same graph. Asserting the reducer over a hand-built two-node graph would prove that
``keep_turns`` appends, which ``tests/serve/test_the_turn_history_is_bounded.py`` does directly.
The pair that can actually break is ``accept``-resets-then-``record``-appends, so both nodes are
here, mounted the way production mounts them.

The one deviation from production is the checkpointer: ``build_serve_graph`` compiles without one
because the server supplies its own, and with no saver there is no second turn to have.
No model, no database — a scripted model and a connector double, as in
``test_the_abstention_policy_is_declared.py``.
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
    """The served topology with a saver, and its session's constants registered as trusted.

    ``trust`` is called here rather than through ``build_serve_graph`` because that function
    compiles with no checkpointer; this is the same two lines it runs, plus the saver the Agent
    server contributes in production.
    """
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
    """The value as a reader of either sink gets it: tuples collapsed to lists, dates to strings.

    ``json.dumps(..., default=str)`` is what the deleted JSONL log wrote, so this
    is not a convenience — it is the shape the log's own reader sees, and the shape msgpack hands
    back out of the checkpoint.
    """
    return json.loads(json.dumps(value, default=str))


def _ask(graph: Any, question: str, *, thread: str) -> dict[str, Any]:
    return graph.invoke(
        {"messages": [HumanMessage(content=question)]},
        {"configurable": {"thread_id": thread}},
    )


def _turns(graph: Any, *, thread: str) -> list[dict[str, Any]]:
    """The channel as the audit reader sees it — through ``get_state``, not through ``invoke``.

    ``output_schema=ServeOutput`` narrows ``invoke`` to ``messages`` / ``answer``, so ``turns``
    is deliberately *not* in the returned dict. ``api/thread_turns`` reads thread state, which
    is what this reads.
    """
    return list(graph.get_state({"configurable": {"thread_id": thread}}).values.get("turns") or [])


def test_two_turns_on_one_thread_both_survive_in_state() -> None:
    """The load-bearing property: turn one is still there after turn two reset everything else.

    The ``answer`` assertion is not decoration — it is what makes the ``turns`` assertion mean
    something. If ``PER_TURN_RESET`` had not run, ``turns`` holding two rows would prove nothing
    about surviving a reset, because there would have been no reset to survive. So the same state
    is asserted to have forgotten turn one's ``answer`` and remembered turn one's envelope.
    """
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
    """The state entry is the durable sink, so the fallible one must not be able to skip it.

    ``record_node`` swallowed everything in one ``try`` and returned ``{}``, which would have made
    a full disk cost the thread its audit trail as well as its log line — the log failing is
    precisely when state is the only copy left.
    """
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
    """The classification, stated where an edit to the reset trips over it rather than passing.

    ``tests/serve/test_state_channels.py`` proves every channel is classified as *something*.
    This proves *which*, and it is the only assertion here that can: mutation-checked
    2026-08-18, adding ``"turns": []`` to ``PER_TURN_RESET`` leaves the two-turn test above
    **green**, because a ``[]`` write under :func:`~governed_bi.serve.state.keep_turns` is a no-op
    — an accumulating channel cannot be cleared through the reset dict at all. So the
    misclassification is invisible behaviourally and only a statement about the classification
    catches it.

    What *is* caught behaviourally is the reducer: dropping the ``Annotated[..., keep_turns]``
    annotation fails the two-turn test, which is the erasure that could really happen.
    """
    assert "turns" in ACCUMULATING
    assert "turns" not in PER_TURN_RESET
