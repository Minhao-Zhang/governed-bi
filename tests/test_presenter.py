"""Tests for the UI-agnostic viz presenter (view models, no UI dependency).

These import only ``governed_bi.viz.presenter``; there is no bundled UI to test.
The interactive frontend is a separate project (see docs/ui-frontend-design.md);
it renders these view models, which the HTTP API (``governed_bi.api``) also serves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.corpus import load_corpus
from governed_bi.server.answer import Answer, ReliabilityTier
from governed_bi.viz import presenter

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"


@pytest.fixture
def corpus():
    # The audit surface reads the FULL corpus (Audit + excluded assets), not for_server.
    return load_corpus(CORPUS_ROOT, db="beer_factory")


def test_corpus_health(corpus):
    health = presenter.corpus_health(corpus)
    assert health.ci_green
    assert health.findings == []
    assert health.counts["table"] == 5
    assert health.n_suspect_columns == 1  # customers.ZipCode
    assert health.n_excluded == 1  # transaction.CreditCardNumber
    assert health.n_low_confidence_joins == 0  # beer_factory joins are 0.95
    assert health.n_skills == 1


def test_table_views_expose_tiers_and_governance(corpus):
    views = {t.id: t for t in presenter.table_views(corpus)}
    assert len(views) == 5

    tx = views["tbl_beer_factory_transaction"]
    ccn = next(c for c in tx.columns if c.physical_name == "CreditCardNumber")
    assert ccn.excluded  # governance.excluded PII column is visible in the audit view
    # Facts + Inference both present on a normal column.
    price = next(c for c in tx.columns if c.physical_name == "PurchasePrice")
    assert price.logical_type and price.description

    customers = views["tbl_beer_factory_customers"]
    zip_col = next(c for c in customers.columns if c.physical_name == "ZipCode")
    assert zip_col.reliability == "suspect"


def test_asset_rows_filterable(corpus):
    rows = presenter.asset_rows(corpus)
    assert all(r.asset_type != "table" for r in rows)  # tables have their own view
    types = {r.asset_type for r in rows}
    assert {"join", "metric", "term"} <= types
    metrics = presenter.asset_rows(corpus, asset_types={"metric"})
    assert metrics and all(r.asset_type == "metric" for r in metrics)


def test_skill_views(corpus):
    skills = presenter.skill_views(corpus)
    assert len(skills) == 1
    assert skills[0].body.strip()


def test_schema_graph_nodes_and_edges(corpus):
    graph = presenter.schema_graph(corpus)
    assert len(graph.nodes) == 5
    customers = next(n for n in graph.nodes if n.physical_name == "customers")
    assert customers.row_count == 554
    assert customers.has_suspect  # ZipCode is suspect
    assert len(graph.edges) == 4
    # beer_factory joins are all 0.95, above the low-confidence threshold.
    assert all(not e.low_confidence for e in graph.edges)
    assert all(e.source and e.target for e in graph.edges)


def test_knowledge_graph_nodes_edges_and_relations(corpus):
    kg = presenter.knowledge_graph(corpus)
    node_ids = {n.id for n in kg.nodes}
    kinds = {n.kind for n in kg.nodes}
    # All asset kinds present in beer_factory show up as nodes.
    assert {"table", "join", "metric", "term"} <= kinds
    # Tables are nodes here too (unlike asset_rows, which excludes them).
    assert "tbl_beer_factory_customers" in node_ids
    # Every edge resolves to real nodes (dangling edges are dropped).
    assert kg.edges and all(e.source in node_ids and e.target in node_ids for e in kg.edges)
    relations = {e.relation for e in kg.edges}
    assert "join" in relations  # join -> its two tables
    assert "measures" in relations  # metric -> base_table
    # A join contributes two edges (to left and right table).
    join_edges = [e for e in kg.edges if e.relation == "join"]
    assert len(join_edges) == 2 * sum(1 for n in kg.nodes if n.kind == "join")


def test_knowledge_graph_dedups_self_join_and_redirects_column_targets():
    # A synthetic corpus exercising two edge-cases the beer_factory corpus lacks:
    # a self-join (both endpoints the same table) and a term/rule that targets a
    # column (not a node). Edges must be deduped, and column targets redirected to
    # the owning table rather than silently dropped.
    from governed_bi.corpus.loader import Corpus
    from governed_bi.corpus.schemas import (
        Column,
        JoinAsset,
        LogicalType,
        RuleAsset,
        RuleKind,
        TableAsset,
        TermAsset,
        TermBinding,
    )

    def _col(name):
        return Column(
            physical_name=name,
            physical_type="INTEGER",
            logical_type=LogicalType.integer,
            nullable=False,
            is_unique=False,
        )

    table = TableAsset(
        id="tbl_x_employees", db="x", physical_name="employees",
        columns=[_col("EmployeeID"), _col("ManagerID")],
    )
    self_join = JoinAsset(
        id="join_x_emp_mgr", left_table="tbl_x_employees", right_table="tbl_x_employees",
        on="employees.ManagerID = employees.EmployeeID",
    )
    term = TermAsset(
        id="term_manager", name="manager",
        binding=TermBinding(asset_type="column", asset_id="col_x_employees_ManagerID"),
    )
    rule = RuleAsset(
        id="rule_mgr", kind=RuleKind.business_rule, statement="managers matter",
        scope=["col_x_employees_ManagerID", "col_x_employees_ManagerID"],  # repeated on purpose
    )
    kg = presenter.knowledge_graph(Corpus(assets=[table, self_join, term, rule], skills=[]))

    edge_ids = [e.id for e in kg.edges]
    assert len(edge_ids) == len(set(edge_ids))  # no colliding edge ids

    joins = [e for e in kg.edges if e.relation == "join"]
    assert len(joins) == 1 and joins[0].target == "tbl_x_employees"  # self-join collapsed

    grounds = [e for e in kg.edges if e.relation == "grounds"]
    assert len(grounds) == 1 and grounds[0].target == "tbl_x_employees"  # column -> owning table

    scopes = [e for e in kg.edges if e.relation == "scopes"]
    assert len(scopes) == 1 and scopes[0].target == "tbl_x_employees"  # redirected + deduped


def test_answer_view_maps_stamp_and_trace():
    answer = Answer(
        tier=ReliabilityTier.governed,
        text="total_revenue = 18496.0",
        sql='SELECT SUM(PurchasePrice) AS total_revenue FROM "transaction"',
        provenance={"route": "kpi_lookup", "metric_id": "metric_revenue"},
    )
    view = presenter.answer_view(answer)
    assert view.tier == "governed"
    assert "SUM(PurchasePrice)" in view.sql
    assert view.provenance["metric_id"] == "metric_revenue"
    assert view.escalation is None
    assert view.result is None  # this answer carried no result grid


def test_answer_view_maps_result_rows():
    from governed_bi.server.answer import ResultTable

    answer = Answer(
        tier=ReliabilityTier.governed,
        text="Total revenue is $18,496.",
        sql='SELECT SUM(PurchasePrice) AS total_revenue FROM "transaction"',
        provenance={},
        result=ResultTable(columns=["total_revenue"], rows=[(18496.0,)], row_count=1),
    )
    view = presenter.answer_view(answer)
    assert view.result is not None
    assert view.result.columns == ["total_revenue"]
    assert view.result.rows == [[18496.0]]  # tuples normalised to lists for rendering
    assert view.result.row_count == 1
    assert view.result.truncated is False
