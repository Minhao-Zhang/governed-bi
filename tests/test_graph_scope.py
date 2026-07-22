"""D15 Phase 2: server-side ER / knowledge-graph scoping.

Unit tests on ``viz.scope`` plus API wiring. Uses the beer_factory corpus for
happy-path filters and a synthetic two-schema corpus for ``boundary`` stubs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from governed_bi.api import create_app  # noqa: E402
from governed_bi.corpus import Corpus  # noqa: E402
from governed_bi.corpus.schemas import (  # noqa: E402
    Cardinality,
    Column,
    JoinAsset,
    LogicalType,
    Reliability,
    TableAsset,
)
from governed_bi.viz import presenter  # noqa: E402
from governed_bi.viz.scope import (  # noqa: E402
    DEFAULT_ER_BUDGET,
    ScopeRequest,
    apply_er_scope,
    apply_kg_scope,
)

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
A_ORDERS = "tbl_schema_a_orders"
B_ORDERS = "tbl_schema_b_orders"
A_ITEMS = "tbl_schema_a_items"


def _col(name: str) -> Column:
    return Column(
        physical_name=name,
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
        reliability=Reliability(),
    )


def _two_schema_corpus(*, with_cross_join: bool = True) -> Corpus:
    """schema_a.orders ↔ schema_b.orders (optional) + schema_a.items → orders."""
    schema_a_orders = TableAsset(
        id=A_ORDERS,
        schema="schema_a",
        physical_name="orders",
        columns=[_col("order_id"), _col("amount")],
    )
    schema_b_orders = TableAsset(
        id=B_ORDERS,
        schema="schema_b",
        physical_name="orders",
        columns=[_col("order_id"), _col("amount")],
    )
    schema_a_items = TableAsset(
        id=A_ITEMS,
        schema="schema_a",
        physical_name="items",
        columns=[_col("item_id"), _col("order_id")],
    )
    assets: list = [schema_a_orders, schema_b_orders, schema_a_items]
    assets.append(
        JoinAsset(
            id="join_schema_a_items_orders",
            left_table=A_ITEMS,
            right_table=A_ORDERS,
            on="schema_a.items.order_id = schema_a.orders.order_id",
            cardinality=Cardinality.many_to_one,
            confidence=0.95,
        )
    )
    if with_cross_join:
        assets.append(
            JoinAsset(
                id="join_schema_a_orders_schema_b_orders",
                left_table=A_ORDERS,
                right_table=B_ORDERS,
                on="schema_a.orders.order_id = schema_b.orders.order_id",
                cardinality=Cardinality.one_to_one,
                confidence=0.99,
            )
        )
    return Corpus(assets=assets)


# --------------------------------------------------------------------------- #
# Unit: apply_er_scope
# --------------------------------------------------------------------------- #


def test_er_unscoped_returns_bare_view():
    view = presenter.schema_graph(_two_schema_corpus())
    out = apply_er_scope(view, req=ScopeRequest())
    assert out is view or (out.meta is None and out.nodes == view.nodes)
    assert out.meta is None


def test_er_schema_filter_and_boundary():
    view = presenter.schema_graph(_two_schema_corpus())
    out = apply_er_scope(view, req=ScopeRequest(schema="schema_a"))
    ids = {n.id for n in out.nodes}
    assert ids == {A_ORDERS, A_ITEMS}
    assert all(n.schema == "schema_a" for n in out.nodes)
    assert out.meta is not None
    assert out.meta.scope is not None
    assert out.meta.scope.schema == "schema_a"
    assert out.meta.scope.node_budget == DEFAULT_ER_BUDGET
    assert out.meta.truncated is False
    # Cross-schema join becomes a boundary stub from schema_a.orders.
    assert len(out.boundary) == 1
    b = out.boundary[0]
    assert b.in_scope_table == A_ORDERS
    assert b.other_schema == "schema_b"
    assert b.other_table_id == B_ORDERS
    assert b.other_label == "orders"


def test_er_focus_radius_neighborhood():
    view = presenter.schema_graph(_two_schema_corpus())
    out = apply_er_scope(
        view, req=ScopeRequest(focus=A_ITEMS, radius=1, node_budget=10)
    )
    ids = {n.id for n in out.nodes}
    # items --1hop--> orders (schema_a); schema_b is 2 hops away.
    assert A_ITEMS in ids
    assert A_ORDERS in ids
    assert B_ORDERS not in ids
    assert out.meta is not None
    assert out.meta.scope is not None
    assert out.meta.scope.focus == A_ITEMS
    assert out.meta.scope.radius == 1
    assert out.meta.scope.node_budget == 10


def test_er_node_budget_truncates_deterministically():
    view = presenter.schema_graph(_two_schema_corpus())
    out = apply_er_scope(view, req=ScopeRequest(schema="schema_a", node_budget=1))
    assert len(out.nodes) == 1
    assert out.meta is not None
    assert out.meta.truncated is True
    assert out.meta.total_nodes == 2
    assert out.meta.returned_nodes == 1
    # Deterministic: only id asc when no focus distances.
    assert out.nodes[0].id == sorted([A_ORDERS, A_ITEMS])[0]


def test_er_budget_hard_cap():
    view = presenter.schema_graph(_two_schema_corpus())
    out = apply_er_scope(view, req=ScopeRequest(schema="schema_a", node_budget=999))
    assert out.meta is not None
    assert out.meta.scope is not None
    assert out.meta.scope.node_budget == DEFAULT_ER_BUDGET  # capped at MAX_ER


# --------------------------------------------------------------------------- #
# Unit: apply_kg_scope
# --------------------------------------------------------------------------- #


def test_kg_schema_pulls_connected_assets():
    view = presenter.knowledge_graph(_two_schema_corpus())
    out = apply_kg_scope(view, req=ScopeRequest(schema="schema_a"))
    ids = {n.id for n in out.nodes}
    assert {A_ORDERS, A_ITEMS} <= ids
    assert "join" in {n.kind for n in out.nodes}  # connected assets pulled in
    assert out.meta is not None
    assert out.meta.scope is not None
    assert out.meta.scope.schema == "schema_a"
    # Far table may be one-hop-pulled via the join (UI parity); if truncated out,
    # the cross-schema join surfaces as a boundary stub instead.
    if B_ORDERS not in ids:
        assert any(b.other_table_id == B_ORDERS for b in out.boundary)


def test_kg_kinds_prefilter():
    view = presenter.knowledge_graph(_two_schema_corpus())
    out = apply_kg_scope(
        view, req=ScopeRequest(kinds=frozenset({"table"}))
    )
    assert all(n.kind == "table" for n in out.nodes)
    assert out.meta is None  # kinds alone is not narrowing


# --------------------------------------------------------------------------- #
# API wiring (beer_factory + synthetic via presenter already covered)
# --------------------------------------------------------------------------- #


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_api_graph_unscoped_still_full(client):
    graph = client.get("/graph").json()
    assert len(graph["nodes"]) == 5
    assert len(graph["edges"]) == 4
    # Unscoped: no meta (back-compat bare envelope).
    assert graph.get("meta") is None


def test_api_graph_schema_filter(client):
    graph = client.get("/graph", params={"schema": "beer_factory"}).json()
    assert len(graph["nodes"]) == 5
    assert all(n["schema"] == "beer_factory" for n in graph["nodes"])
    meta = graph["meta"]
    assert meta["truncated"] is False
    assert meta["scope"]["schema"] == "beer_factory"
    assert meta["scope"]["node_budget"] == DEFAULT_ER_BUDGET


def test_api_graph_focus_and_budget(client):
    focus = "tbl_beer_factory_customers"
    graph = client.get(
        "/graph",
        params={"focus": focus, "radius": 1, "node_budget": 3},
    ).json()
    assert focus in {n["id"] for n in graph["nodes"]}
    assert len(graph["nodes"]) <= 3
    assert graph["meta"]["scope"]["focus"] == focus
    assert graph["meta"]["scope"]["radius"] == 1
    assert graph["meta"]["scope"]["node_budget"] == 3


def test_api_knowledge_graph_kinds(client):
    kg = client.get("/knowledge-graph", params={"kinds": "table,join"}).json()
    assert kg["nodes"]
    assert {n["kind"] for n in kg["nodes"]} <= {"table", "join"}


def test_api_knowledge_graph_schema(client):
    kg = client.get("/knowledge-graph", params={"schema": "beer_factory"}).json()
    assert kg["meta"]["scope"]["schema"] == "beer_factory"
    tables = [n for n in kg["nodes"] if n["kind"] == "table"]
    assert tables and all(n["schema"] == "beer_factory" for n in tables)
