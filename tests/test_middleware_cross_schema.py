"""Middleware enforces the D15 curated cross-schema-join guarantee (audit S5).

The agent can self-license a table in another schema via ``inspect_schema``; the
retrieval-time missing-edge refusal does not cover that, so ``run_query`` re-checks
at execution. Must be a strict NO-OP for a single-schema (BIRD/demo) query.
"""

from __future__ import annotations

from governed_bi.analyst.middleware import GovernanceMiddleware
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
