"""Tests for the graph layer: corpus -> networkx projection, Steiner planning.

Runs against the committed ``corpus/beer_factory`` semantic layer, so no live
database is needed.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from governed_bi.corpus import load_corpus
from governed_bi.graph import (
    EDGE_BINDS_TO,
    EDGE_DERIVED_FROM,
    EDGE_HAS_COLUMN,
    EDGE_JOINS_TO,
    EDGE_REFERENCES,
    NODE_COLUMN,
    NODE_METRIC,
    NODE_TABLE,
    NODE_TERM,
    build_graph,
    join_neighborhood,
    plan_joins,
)

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"


@pytest.fixture
def graph():
    corpus = load_corpus(CORPUS_ROOT, db="beer_factory").for_server()
    return build_graph(corpus)


def _edges_of_type(g, edge_type):
    return [(u, v, d) for u, v, d in g.edges(data=True) if d["type"] == edge_type]


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #


def test_node_kinds_and_counts(graph):
    kinds = {}
    for _, data in graph.nodes(data=True):
        kinds[data["kind"]] = kinds.get(data["kind"], 0) + 1
    assert kinds[NODE_TABLE] == 5
    assert kinds[NODE_TERM] == 2
    assert kinds[NODE_METRIC] == 2
    assert kinds[NODE_COLUMN] > 0


def test_no_phantom_nodes(graph):
    """Every node was created in pass 1, so all carry a ``kind`` attribute."""
    kindless = [n for n, d in graph.nodes(data=True) if "kind" not in d]
    assert kindless == [], kindless


def test_table_node_carries_facts(graph):
    data = graph.nodes["tbl_beer_factory_transaction"]
    assert data["kind"] == NODE_TABLE
    assert data["physical_name"] == "transaction"
    assert data["db"] == "beer_factory"


# --------------------------------------------------------------------------- #
# Edges
# --------------------------------------------------------------------------- #


def test_has_column_edges(graph):
    tid = "tbl_beer_factory_customers"
    cid = "col_beer_factory_customers_CustomerID"
    assert graph.has_edge(tid, cid, key=EDGE_HAS_COLUMN)
    # every HAS_COLUMN edge runs table -> column
    for u, v, _ in _edges_of_type(graph, EDGE_HAS_COLUMN):
        assert graph.nodes[u]["kind"] == NODE_TABLE
        assert graph.nodes[v]["kind"] == NODE_COLUMN


def test_joins_to_edges_carry_planner_props(graph):
    joins = _edges_of_type(graph, EDGE_JOINS_TO)
    assert len(joins) == 4
    by_id = {d["join_id"]: d for _, _, d in joins}
    jtc = by_id["join_transaction_customers"]
    assert jtc["cost"] == 1.0
    assert jtc["confidence"] == 0.95
    assert jtc["cardinality"] == "many_to_one"


def test_references_edges(graph):
    # transaction.CustomerID -> customers.CustomerID
    src = "col_beer_factory_transaction_CustomerID"
    dst = "col_beer_factory_customers_CustomerID"
    assert graph.has_edge(src, dst, key=EDGE_REFERENCES)


def test_term_binding_and_relation_edges(graph):
    # term_revenue BINDS_TO metric_revenue, and USES term_brand.
    assert graph.has_edge("term_revenue", "metric_revenue", key=EDGE_BINDS_TO)
    assert graph.has_edge("term_revenue", "term_brand", key="USES")
    uses = graph.get_edge_data("term_revenue", "term_brand", key="USES")
    assert uses["relation"] == "uses"


def test_metric_derived_from_base_table(graph):
    assert graph.has_edge("metric_revenue", "tbl_beer_factory_transaction", key=EDGE_DERIVED_FROM)


def test_excluded_column_absent_in_server_graph(graph):
    """The PII column is governance.excluded, so for_server() drops it and the
    server-facing graph must not contain its node."""
    assert "col_beer_factory_transaction_CreditCardNumber" not in graph.nodes


def test_no_phantom_nodes_from_excluded_reference():
    """for_server() can leave a join/FK pointing at an excluded asset; build_graph
    must not resurface it as a bare, kind-less node."""
    from governed_bi.corpus import Corpus
    from governed_bi.corpus.ids import derive_column_id
    from governed_bi.corpus.schemas import Column, Governance, JoinAsset, LogicalType, TableAsset

    b_pk = derive_column_id("tbl_x_b", "id")
    a = TableAsset(
        id="tbl_x_a",
        db="x",
        physical_name="a",
        columns=[
            Column(
                physical_name="b_id",
                physical_type="int",
                logical_type=LogicalType.integer,
                nullable=True,
                is_unique=False,
                references=b_pk,  # FK into the excluded table's column
            )
        ],
    )
    b = TableAsset(
        id="tbl_x_b",
        db="x",
        physical_name="b",
        governance=Governance(excluded=True),
        columns=[
            Column(
                physical_name="id",
                physical_type="int",
                logical_type=LogicalType.integer,
                nullable=False,
                is_unique=True,
            )
        ],
    )
    join = JoinAsset(id="join_a_b", left_table="tbl_x_a", right_table="tbl_x_b", on="a.b_id = b.id")

    g = build_graph(Corpus(assets=[a, b, join]).for_server())
    assert [n for n, d in g.nodes(data=True) if "kind" not in d] == []  # no phantom nodes
    assert "tbl_x_b" not in g.nodes  # excluded table did not resurface
    assert b_pk not in g.nodes


# --------------------------------------------------------------------------- #
# Steiner join planning
# --------------------------------------------------------------------------- #

# Short aliases for the beer_factory table ids used in planning assertions.
CUSTOMERS = "tbl_beer_factory_customers"
TRANSACTION = "tbl_beer_factory_transaction"
ROOTBEER = "tbl_beer_factory_rootbeer"
BRAND = "tbl_beer_factory_rootbeerbrand"
REVIEW = "tbl_beer_factory_rootbeerreview"


def _synthetic_join_graph(tables, joins):
    """Minimal graph shaped like a projection: table nodes + JOINS_TO edges.

    ``joins`` items are ``(join_id, left, right, cost, confidence)``.
    """
    g = nx.MultiDiGraph()
    for t in tables:
        g.add_node(t, kind=NODE_TABLE)
    for join_id, left, right, cost, confidence in joins:
        g.add_edge(
            left,
            right,
            key=join_id,
            type=EDGE_JOINS_TO,
            join_id=join_id,
            cost=cost,
            confidence=confidence,
        )
    return g


def test_plan_single_table_needs_no_joins(graph):
    plan = plan_joins(graph, {CUSTOMERS})
    assert plan.join_ids == []
    assert plan.min_confidence == 1.0


def test_plan_direct_join(graph):
    plan = plan_joins(graph, {TRANSACTION, CUSTOMERS})
    assert plan.join_ids == ["join_transaction_customers"]
    assert plan.min_confidence == 0.95


def test_plan_pulls_in_steiner_point(graph):
    # customers and rootbeer only connect through transaction.
    plan = plan_joins(graph, {CUSTOMERS, ROOTBEER})
    assert plan.join_ids == ["join_transaction_customers", "join_transaction_rootbeer"]


def test_plan_full_path(graph):
    plan = plan_joins(graph, {CUSTOMERS, REVIEW})
    assert set(plan.join_ids) == {
        "join_transaction_customers",
        "join_transaction_rootbeer",
        "join_rootbeer_rootbeerbrand",
        "join_review_rootbeerbrand",
    }


def test_plan_order_is_incremental(graph):
    """Each emitted join attaches a new table to the already-connected set."""
    plan = plan_joins(graph, {CUSTOMERS, ROOTBEER})
    # first join touches the start table; second bridges to the far table.
    assert plan.join_ids[0] == "join_transaction_customers"


def test_plan_unknown_table_raises(graph):
    with pytest.raises(ValueError, match="not table nodes"):
        plan_joins(graph, {CUSTOMERS, "tbl_beer_factory_ghost"})


def test_plan_disconnected_raises():
    # X-Y joined; Z is an isolated table with no join edge.
    g = _synthetic_join_graph(
        ["X", "Y", "Z"],
        [("join_xy", "X", "Y", 1.0, 0.9)],
    )
    with pytest.raises(ValueError, match="not connected"):
        plan_joins(g, {"X", "Z"})


def test_plan_joins_with_unrelated_isolated_table_does_not_crash():
    # A disconnected table unrelated to the required set must not crash steiner_tree.
    g = _synthetic_join_graph(
        ["X", "Y", "Z"],
        [("join_xy", "X", "Y", 1.0, 0.9)],
    )
    plan = plan_joins(g, {"X", "Y"})
    assert plan.join_ids == ["join_xy"]
    assert plan.min_confidence == 0.9


# --------------------------------------------------------------------------- #
# FK join-neighborhood (L4 licensing decoupled from retrieval recall)
# --------------------------------------------------------------------------- #


def test_join_neighborhood_hops1(graph):
    # customers joins only to transaction, so its 1-hop neighborhood is just those
    # two (and includes the input id itself).
    assert join_neighborhood(graph, {CUSTOMERS}, hops=1) == {CUSTOMERS, TRANSACTION}


def test_join_neighborhood_hops2_reaches_further(graph):
    # A second hop from transaction pulls in rootbeer.
    assert join_neighborhood(graph, {CUSTOMERS}, hops=2) == {CUSTOMERS, TRANSACTION, ROOTBEER}


def test_join_neighborhood_hops1_excludes_far_table(graph):
    # rootbeerbrand is 3 hops from customers; a 1-hop neighborhood must not reach it.
    assert BRAND not in join_neighborhood(graph, {CUSTOMERS}, hops=1)


def test_join_neighborhood_unknown_id_is_empty(graph):
    # An id that is not a table node contributes nothing (it is ignored).
    assert join_neighborhood(graph, {"tbl_beer_factory_ghost"}, hops=1) == set()


def test_join_neighborhood_ignores_unknown_keeps_valid(graph):
    # A mix of one valid and one unknown id yields only the valid id's neighborhood.
    assert join_neighborhood(graph, {CUSTOMERS, "tbl_beer_factory_ghost"}, hops=1) == {
        CUSTOMERS,
        TRANSACTION,
    }


def test_low_confidence_join_is_penalized():
    # Direct A-B is cheap but low-confidence; the A-C-B detour is high-confidence
    # and, after the penalty, cheaper. The planner must avoid the direct edge.
    g = _synthetic_join_graph(
        ["A", "B", "C"],
        [
            ("join_ab", "A", "B", 1.0, 0.1),  # weight 1.0*(1+0.9) = 1.9
            ("join_ac", "A", "C", 0.4, 1.0),  # weight 0.4
            ("join_cb", "C", "B", 0.4, 1.0),  # weight 0.4  -> detour total 0.8
        ],
    )
    plan = plan_joins(g, {"A", "B"})
    assert set(plan.join_ids) == {"join_ac", "join_cb"}
    assert plan.min_confidence == 1.0  # the 0.1 edge was avoided
