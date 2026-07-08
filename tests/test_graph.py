"""Tests for the graph layer: corpus -> networkx projection, Steiner planning.

Runs against the committed ``corpus/beer_factory`` semantic layer, so no live
database is needed.
"""

from __future__ import annotations

from pathlib import Path

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
