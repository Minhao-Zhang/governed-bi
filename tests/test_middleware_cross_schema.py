"""Middleware enforces the D15 curated cross-schema-join guarantee (audit S5).

The agent can self-license a table in another schema via ``inspect_schema``; the
retrieval-time missing-edge refusal does not cover that, so ``run_query`` re-checks
at execution. Must be a strict NO-OP for a single-schema (BIRD/demo) query.

Also covers the fail-closed detector path (AUDIT R5) and the hard-stop at the
``run_query`` choke point (AUDIT T1) — not only the private detector helper.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from governed_bi.analyst.agent import build_agent_core
from governed_bi.analyst.middleware import GovernanceHardStop, GovernanceMiddleware
from governed_bi.config import Environment, Settings
from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import (
    Cardinality,
    Column,
    JoinAsset,
    LogicalType,
    TableAsset,
)
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn

_XSCHEMA_SQL = (
    "SELECT a.order_id FROM schema_a.orders AS a "
    "JOIN schema_b.orders AS b ON a.order_id = b.order_id"
)


def _col(name: str) -> Column:
    return Column(
        physical_name=name,
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
    )


def _tables() -> tuple[TableAsset, TableAsset]:
    a = TableAsset(
        id="tbl_schema_a_orders",
        schema="schema_a",
        physical_name="orders",
        columns=[_col("order_id"), _col("amount")],
    )
    b = TableAsset(
        id="tbl_schema_b_orders",
        schema="schema_b",
        physical_name="orders",
        columns=[_col("order_id"), _col("amount")],
    )
    return a, b


def _cross_join() -> JoinAsset:
    return JoinAsset(
        id="join_schema_a_orders_schema_b_orders",
        left_table="tbl_schema_a_orders",
        right_table="tbl_schema_b_orders",
        on="schema_a.orders.order_id = schema_b.orders.order_id",
        cardinality=Cardinality.one_to_one,
        confidence=0.99,
    )


def _mw(assets: list) -> tuple[GovernanceMiddleware, SqliteConnector]:
    conn = SqliteConnector(":memory:")
    mw = GovernanceMiddleware(
        Corpus(assets=assets),
        Gateway(conn),
        Identity(user="u", all_access=True),
        dialect="postgres",
        default_schema=None,
        settings=Settings.for_env(Environment.dev),
    )
    return mw, conn


def _agent(assets: list, responses):
    conn = SqliteConnector(":memory:")
    corpus = Corpus(assets=assets)
    gateway = Gateway(conn)
    agent = build_agent_core(
        corpus,
        gateway,
        Identity(user="u", all_access=True),
        FakeToolModel(responses=responses),
        settings=Settings.for_env(Environment.dev),
        dialect="postgres",
        default_schema=None,
    )
    return agent, conn


def test_cross_schema_without_curated_join_is_flagged():
    mw, conn = _mw(list(_tables()))
    try:
        missing = mw._cross_schema_missing_join(_XSCHEMA_SQL)
        assert missing is not None
        assert missing.schemas == frozenset({"schema_a", "schema_b"})
    finally:
        conn.close()


def test_cross_schema_with_curated_join_is_allowed():
    a, b = _tables()
    mw, conn = _mw([a, b, _cross_join()])
    try:
        assert mw._cross_schema_missing_join(_XSCHEMA_SQL) is None
    finally:
        conn.close()


def test_single_schema_query_is_noop():
    a, _ = _tables()
    items = TableAsset(
        id="tbl_schema_a_items",
        schema="schema_a",
        physical_name="items",
        columns=[_col("item_id"), _col("order_id")],
    )
    mw, conn = _mw([a, items])
    sql = (
        "SELECT a.order_id FROM schema_a.orders AS a "
        "JOIN schema_a.items AS i ON a.order_id = i.order_id"
    )
    try:
        assert mw._cross_schema_missing_join(sql) is None
    finally:
        conn.close()


def test_run_query_hard_stops_on_cross_schema_without_curated_join():
    """T1: drive ``run_query`` through middleware so the D15 ``GovernanceHardStop``
    at the choke point is covered — not only the private detector helper.

    Replacing the hard-stop raise with ``pass`` would let this cross-schema join
    reach execute; this test must fail in that case.
    """
    a, b = _tables()
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": a.id}, "c1"),
        ai_tool_turn("inspect_schema", {"table_id": b.id}, "c2"),
        ai_tool_turn("run_query", {"sql": _XSCHEMA_SQL}, "c3"),
    ]
    agent, conn = _agent([a, b], turns)
    try:
        with pytest.raises(GovernanceHardStop) as ei:
            agent.invoke(
                {"messages": [HumanMessage("cross")], "licensed": [], "ledger": []}
            )
        assert ei.value.entry["verdict"] == "block"
        assert ei.value.entry["action"] == "run_query"
        assert "D15 missing edge" in ei.value.entry["reason"]
        # Never reached execute — no pass/error ledger entry for this attempt.
        assert ei.value.entry.get("result") is None
    finally:
        conn.close()


def test_cross_schema_detector_exception_blocks_query(monkeypatch):
    """R5: detector crash must fail-closed — block, not execute."""
    a, b = _tables()
    # Curated join present so a healthy detector would allow; only the boom path
    # should fire the hard stop.
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": a.id}, "c1"),
        ai_tool_turn("inspect_schema", {"table_id": b.id}, "c2"),
        ai_tool_turn("run_query", {"sql": _XSCHEMA_SQL}, "c3"),
    ]
    agent, conn = _agent([a, b, _cross_join()], turns)

    def _boom(self, sql: str):  # noqa: ANN001
        raise RuntimeError("detector boom")

    monkeypatch.setattr(
        GovernanceMiddleware, "_cross_schema_missing_join", _boom
    )
    try:
        with pytest.raises(GovernanceHardStop) as ei:
            agent.invoke(
                {"messages": [HumanMessage("cross")], "licensed": [], "ledger": []}
            )
        assert ei.value.entry["verdict"] == "block"
        assert "D15 fail-closed" in ei.value.entry["reason"]
        assert "RuntimeError" in ei.value.entry["reason"]
        assert ei.value.entry.get("result") is None
    finally:
        conn.close()
