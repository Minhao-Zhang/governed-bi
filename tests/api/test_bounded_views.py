"""``subgraph``: what a bounded relationship view must say about what it left out.

Every test here is about one failure mode — **a partial corpus that reads as a complete
one**. ADR 0009 D2 names it, and each of these has occurred:

* a scope echo missing ``node_budget``, so the client could not tell that the engine had
  already truncated and re-truncated the payload itself, overwriting ``truncated: True,
  dropped: 7827`` with ``false``/``0``;
* a budget spent on an alphabetical prefix, which returned 150 nodes and **zero** edges from a
  graph of 216 — a relationship view showing no relationships;
* a cross-namespace join dropped without trace, which draws a joined table as isolated.

The scoping is pure, so none of this needs a server or a corpus.
"""

from __future__ import annotations

from typing import Any

from governed_bi.api.browse import subgraph


def _node(node_id: str, schema: str, **extra: Any) -> dict[str, Any]:
    return {"id": node_id, "label": node_id.split(".")[-1], "schema": schema, **extra}


def _join(source: str, target: str, on: str) -> dict[str, Any]:
    return {
        "id": f"{source}->{target}",
        "source": source,
        "target": target,
        "on": on,
        "cardinality": "many_to_one",
        "confidence": None,
    }


def test_the_applied_scope_echo_carries_the_budget() -> None:
    """The client compares the echoed scope field-for-field against what it asked for, and
    re-scopes the payload itself when they differ — discarding ``truncated``/``dropped`` when
    it does. ``node_budget`` sat one level up in ``meta`` while the comparison read it from
    ``meta.scope``, so the comparison could never succeed and the budget was never honest.
    """
    view = subgraph(nodes=[_node("a.one", "a")], edges=[], schema="a", node_budget=7)
    assert view["meta"]["scope"]["node_budget"] == 7, view["meta"]["scope"]
    # Still where it was, too: removing it from `meta` would be a second break.
    assert view["meta"]["node_budget"] == 7


def test_truncation_keeps_a_connected_picture_not_an_alphabetical_prefix() -> None:
    """A star: one hub joined to three leaves, plus three isolated tables sorting *earlier*.

    Ordering by id would spend a budget of 4 on ``aaa``/``aab``/``aac`` and the hub, returning
    four nodes and no edges. What makes a relationship view worth rendering is that the budget
    buys nodes that are actually related, so the hub and its leaves must survive together.
    """
    nodes = (
        [_node(f"s.aa{c}", "s") for c in "abc"] + [_node("s.hub", "s")] + [_node(f"s.leaf{i}", "s") for i in (1, 2, 3)]
    )
    edges = [_join("s.hub", f"s.leaf{i}", f"hub.id = leaf{i}.hub_id") for i in (1, 2, 3)]

    view = subgraph(nodes=nodes, edges=edges, node_budget=4)
    kept = {n["id"] for n in view["nodes"]}
    assert kept == {"s.hub", "s.leaf1", "s.leaf2", "s.leaf3"}, kept
    assert len(view["edges"]) == 3, "the budget bought a connected component, so its edges came too"
    assert view["meta"]["truncated"] is True
    assert view["meta"]["dropped"] == 3


def test_an_edgeless_corpus_still_returns_its_nodes_in_id_order() -> None:
    """Degree-ordering must not become a way to return nothing. With no edges every node has
    degree 0, and the answer is the old alphabetical page — not an empty view."""
    nodes = [_node(f"s.t{i}", "s") for i in (3, 1, 2)]
    view = subgraph(nodes=nodes, edges=[], node_budget=2)
    assert [n["id"] for n in view["nodes"]] == ["s.t1", "s.t2"]


def test_a_join_leaving_the_namespace_is_reported_as_a_destination() -> None:
    """The one thing a scoped view cannot do is drop a cross-namespace join silently.

    ``sales.orders`` joins ``people.customers``. Scoped to ``sales``, the edge is gone from
    ``edges`` — both endpoints have to be in scope for a line to be drawn — so without the
    boundary list ``orders`` renders as an isolated table, which is a claim about the schema
    rather than about the window. A cross-schema join executes, so the stub is a place to
    navigate to and carries no severity.
    """
    nodes = [_node("sales.orders", "sales"), _node("people.customers", "people")]
    edges = [_join("sales.orders", "people.customers", "orders.customer_id = customers.id")]

    view = subgraph(nodes=nodes, edges=edges, schema="sales")
    assert [n["id"] for n in view["nodes"]] == ["sales.orders"]
    assert view["edges"] == [], "an edge with one endpoint out of scope is not drawable"

    boundary = view["boundary"]
    assert len(boundary) == 1, boundary
    stub = boundary[0]
    assert stub["in_scope_table"] == "sales.orders"
    assert stub["other_schema"] == "people"
    assert stub["other_table_id"] == "people.customers"
    assert stub["other_label"] == "customers"
    assert stub["on"] == "orders.customer_id = customers.id", "the predicate is the point"


def test_a_join_inside_one_namespace_is_not_a_boundary() -> None:
    """Only a *namespace* crossing qualifies. A join dropped because the budget bit is not a
    boundary — calling it one would turn every truncation into a fake cross-schema finding."""
    nodes = [_node("s.a", "s"), _node("s.b", "s")]
    edges = [_join("s.a", "s.b", "a.id = b.a_id")]
    assert subgraph(nodes=nodes, edges=edges, node_budget=1)["boundary"] == []


def test_a_semantic_reference_across_namespaces_is_not_a_destination() -> None:
    """A term grounding a column in another namespace is not somewhere you can navigate to and
    back, and the panel that renders these is about joins. Only join-bearing edges qualify."""
    nodes = [_node("term.revenue", "sales"), _node("people.customers", "people")]
    edges = [
        {"id": "e", "source": "term.revenue", "target": "people.customers", "relation": "grounds", "confidence": None}
    ]
    assert subgraph(nodes=nodes, edges=edges, schema="sales")["boundary"] == []


def test_several_joins_to_one_far_table_are_one_destination() -> None:
    """Several relationships between a table pair is the normal case; it is still one place to
    go. Two stubs for one destination would double-count the crossing in the panel."""
    nodes = [_node("sales.orders", "sales"), _node("people.customers", "people")]
    edges = [
        _join("sales.orders", "people.customers", "orders.customer_id = customers.id"),
        _join("sales.orders", "people.customers", "orders.billed_to = customers.id"),
    ]
    boundary = subgraph(nodes=nodes, edges=edges, schema="sales")["boundary"]
    assert len(boundary) == 1, boundary


# ── GET /columns/{column_id}/related ─────────────────────────────────────────


def _related(session: Any, column_id: str) -> dict[str, Any]:
    from governed_bi.api import browse_routes

    original = browse_routes._request_session
    browse_routes._request_session = lambda: session  # type: ignore[assignment]
    try:
        return browse_routes.column_related(column_id)
    finally:
        browse_routes._request_session = original  # type: ignore[assignment]


def _column_session() -> Any:
    """Two tables joined on ``customer_id = id``, a term bound to one column, a table rule."""
    from governed_bi.corpus.schema import (
        AssetType,
        Binding,
        Cardinality,
        ColumnAsset,
        JoinAsset,
        MetricAsset,
        TableAsset,
        TermAsset,
    )
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.session import Session

    orders = TableAsset(
        id="sales.orders",
        schema="sales",
        physical_name="orders",
        summary="Orders.",
        columns=("sales.orders.customer_id", "sales.orders.id"),
        rules=("Exclude cancelled orders from revenue.",),
    )
    customers = TableAsset(
        id="people.customers",
        schema="people",
        physical_name="customers",
        summary="Customers.",
        columns=("people.customers.id",),
    )
    fk = ColumnAsset(
        id="sales.orders.customer_id",
        schema="sales",
        parent_table="sales.orders",
        physical_name="customer_id",
        summary="FK to customers.",
        references="people.customers.id",
    )
    order_id = ColumnAsset(
        id="sales.orders.id",
        schema="sales",
        parent_table="sales.orders",
        physical_name="id",
        summary="Order id.",
    )
    customer_id = ColumnAsset(
        id="people.customers.id",
        schema="people",
        parent_table="people.customers",
        physical_name="id",
        summary="Customer id.",
    )
    join = JoinAsset(
        id="join_sales_orders_customers_abc12345",
        left_table="sales.orders",
        right_table="people.customers",
        on="orders.customer_id = customers.id",
        summary="Orders to customers.",
        cardinality=Cardinality.many_to_one,
    )
    term = TermAsset(
        id="term.customer",
        name="customer",
        summary="customer, buyer",
        binding=Binding(target_type=AssetType.column, target_id="sales.orders.customer_id"),
        synonyms=("buyer",),
    )
    metric = MetricAsset(
        id="metric.order_count",
        name="order count",
        base_table="sales.orders",
        expression="COUNT(*)",
        summary="Orders counted.",
    )
    assets = (orders, customers, fk, order_id, customer_id, join, term, metric)
    return Session(
        index=None,
        structure=None,
        assets_by_id={a.id: a for a in assets},
        corpus=None,
        connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}),
        corpus_content_hash="c",
        prompt_set_hash="p",
        knobs_resolved={},
        db_id="d",
        run_id="r",
    )


def test_an_unknown_column_id_is_an_answer_not_a_404() -> None:
    """The client's query declares ``retry: false`` and renders ``column_resolvable`` as a
    sentence, so an unknown id must come back as a fact. A 404 renders as a broken panel, and
    the two say different things: the sheet is reached by *clicking a column*, so an id that
    does not resolve means the id scheme drifted — which is worth saying rather than hiding.
    """
    payload = _related(_column_session(), "sales.orders.nope")
    assert payload["meta"]["column_resolvable"] is False
    assert payload["column"]["id"] == "sales.orders.nope", "echo the id that failed"
    assert payload["terms"] == [] and payload["joins"] == [] and payload["fk_out"] is None


def test_a_column_reports_the_semantic_layer_that_touches_it() -> None:
    payload = _related(_column_session(), "sales.orders.customer_id")

    assert payload["meta"]["column_resolvable"] is True
    assert payload["column"]["table_physical_name"] == "orders"
    assert [t["name"] for t in payload["terms"]] == ["customer"]
    assert payload["terms"][0]["synonyms"] == ["buyer"]
    assert payload["fk_out"] == {
        "column_id": "people.customers.id",
        "table_id": "people.customers",
        "physical_name": "id",
    }
    assert [j["id"] for j in payload["joins"]] == ["join_sales_orders_customers_abc12345"]
    assert payload["joins"][0]["other_table_id"] == "people.customers"
    assert payload["joins"][0]["cardinality"] == "many_to_one"
    # Table-grain: a metric over this table relates to every one of its columns.
    assert [m["id"] for m in payload["metrics"]] == ["metric.order_count"]
    # The table's normative text, which no route has ever emitted, with a POSITIONAL id --
    # these are strings in a tuple, and minting an asset-looking id would invent an identity.
    assert payload["rules"] == [
        {
            "id": "sales.orders#rule-0",
            "kind": "table",
            "statement": "Exclude cancelled orders from revenue.",
            "confidence": None,
            "provenance_status": None,
        }
    ]


def test_a_join_is_matched_by_parsing_the_predicate_not_by_scanning_it() -> None:
    """``sales.orders.id`` must NOT claim the ``orders.customer_id = customers.id`` join
    through its own table's side: ``id`` occurs inside ``customer_id`` as a substring, and a
    substring match would assert a relationship that does not exist. It *does* claim it via
    the other side only if that side is this column — which it is not.
    """
    payload = _related(_column_session(), "sales.orders.id")
    assert payload["joins"] == [], "orders.id is named nowhere in the predicate; a substring scan would have matched it"

    # The far side's `customers.id` IS in the predicate, qualified by its own table.
    far = _related(_column_session(), "people.customers.id")
    assert [j["id"] for j in far["joins"]] == ["join_sales_orders_customers_abc12345"]
    assert far["joins"][0]["other_table_id"] == "sales.orders", "the *other* end, from here"


def test_the_reverse_foreign_key_is_reported_on_the_target() -> None:
    """``fk_in`` is what makes the panel navigable in both directions."""
    payload = _related(_column_session(), "people.customers.id")
    assert payload["fk_out"] is None
    assert payload["fk_in"] == [
        {
            "column_id": "sales.orders.customer_id",
            "table_id": "sales.orders",
            "physical_name": "customer_id",
        }
    ]
