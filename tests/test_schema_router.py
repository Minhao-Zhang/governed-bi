"""D15 join-aware schema router (retrieval pre-stage)."""

from __future__ import annotations

from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import (
    Cardinality,
    Column,
    JoinAsset,
    LogicalType,
    Reliability,
    TableAsset,
)
from governed_bi.retrieval import (
    expand_schemas_via_curated_joins,
    filter_corpus_for_retrieval,
    retrieve,
    route_schemas,
    shortlist_schemas,
)


def _col(name: str) -> Column:
    return Column(
        physical_name=name,
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
        reliability=Reliability(),
    )


def _table(tid: str, schema: str, physical: str, *cols: str, description: str = "") -> TableAsset:
    return TableAsset(
        id=tid,
        schema=schema,
        physical_name=physical,
        description=description or None,
        columns=[_col(c) for c in cols],
    )


def _three_schema_bridge() -> Corpus:
    """sales.orders ↔ ops.bridge ↔ finance.invoices (curated cross-schema joins).

    Lexical content is deliberately partitioned so a question about "invoices"
    shortlists finance, and join expansion must pull ops (and optionally sales).
    """
    sales_orders = _table(
        "tbl_sales_orders",
        "sales",
        "orders",
        "order_id",
        description="customer purchase orders sales pipeline",
    )
    ops_bridge = _table(
        "tbl_ops_bridge",
        "ops",
        "bridge",
        "order_id",
        "invoice_id",
        description="ops fulfillment bridge linking orders to invoices",
    )
    finance_invoices = _table(
        "tbl_finance_invoices",
        "finance",
        "invoices",
        "invoice_id",
        "amount",
        description="finance accounts payable invoices billing",
    )
    # Unrelated schema that should not enter a finance-focused shortlist.
    hr_employees = _table(
        "tbl_hr_employees",
        "hr",
        "employees",
        "employee_id",
        description="human resources payroll employees headcount",
    )
    join_sales_ops = JoinAsset(
        id="join_sales_orders_ops_bridge",
        left_table="tbl_sales_orders",
        right_table="tbl_ops_bridge",
        on="sales.orders.order_id = ops.bridge.order_id",
        cardinality=Cardinality.one_to_one,
        confidence=0.99,
    )
    join_ops_finance = JoinAsset(
        id="join_ops_bridge_finance_invoices",
        left_table="tbl_ops_bridge",
        right_table="tbl_finance_invoices",
        on="ops.bridge.invoice_id = finance.invoices.invoice_id",
        cardinality=Cardinality.one_to_one,
        confidence=0.99,
    )
    return Corpus(
        assets=[
            sales_orders,
            ops_bridge,
            finance_invoices,
            hr_employees,
            join_sales_ops,
            join_ops_finance,
        ]
    )


def test_shortlist_prefers_lexically_matching_schema():
    corpus = _three_schema_bridge()
    top = shortlist_schemas(corpus, "total invoice billing amount", top_k=1)
    assert top == ["finance"]


def test_expand_pulls_bridge_schema_via_curated_joins():
    corpus = _three_schema_bridge()
    expanded = expand_schemas_via_curated_joins(corpus, {"finance"})
    # finance ↔ ops ↔ sales
    assert expanded == frozenset({"finance", "ops", "sales"})
    assert "hr" not in expanded


def test_route_schemas_composes_shortlist_and_expansion():
    corpus = _three_schema_bridge()
    routed = route_schemas(corpus, "invoice billing amount", top_k=1)
    assert "finance" in routed
    assert "ops" in routed  # bridge
    assert "hr" not in routed


def test_filter_corpus_keeps_only_routed_schemas_and_their_joins():
    corpus = _three_schema_bridge()
    routed = frozenset({"finance", "ops"})
    filtered = filter_corpus_for_retrieval(corpus, routed)
    ids = {a.id for a in filtered.assets}
    assert "tbl_finance_invoices" in ids
    assert "tbl_ops_bridge" in ids
    assert "tbl_hr_employees" not in ids
    assert "tbl_sales_orders" not in ids
    # Cross join finance↔ops kept; sales↔ops dropped (sales not routed).
    assert "join_ops_bridge_finance_invoices" in ids
    assert "join_sales_orders_ops_bridge" not in ids


def test_retrieve_after_routing_excludes_unrelated_schema():
    corpus = _three_schema_bridge()
    routed = route_schemas(corpus, "invoice billing amount", top_k=1)
    filtered = filter_corpus_for_retrieval(corpus, routed)
    result = retrieve(filtered, "invoice billing amount", top_k=8)
    assert "tbl_hr_employees" not in result.table_ids
    assert "tbl_finance_invoices" in result.table_ids


def test_route_schemas_recorded_in_provenance(monkeypatch):
    """Multi-schema serve path stamps routed_schemas into answer provenance."""
    from dataclasses import replace

    from governed_bi.config import DataSourceConfig, Environment, Settings
    from governed_bi.gateway import Gateway, Identity, SqliteConnector
    from governed_bi.retrieval import RetrievalResult
    from governed_bi.server import answer_question

    corpus = _three_schema_bridge().for_server()
    settings = replace(
        Settings.for_env(Environment.dev),
        datasource=DataSourceConfig(kind="postgres", dsn="host=x"),
    )

    def _fake_retrieve(corpus_arg, question, *, embedder=None):
        return RetrievalResult(
            question=question,
            table_ids=["tbl_finance_invoices"],
            metric_ids=[],
            term_ids=[],
            few_shot_ids=[],
            scores={},
        )

    monkeypatch.setattr("governed_bi.server.flow.retrieve", _fake_retrieve)

    conn = SqliteConnector(":memory:")
    try:
        ans = answer_question(
            "invoice billing amount",
            Identity(user="dev", all_access=True),
            corpus=corpus,
            gateway=Gateway(conn),
            settings=settings,
            session_id="s",
        )
    finally:
        conn.close()

    assert "routed_schemas" in ans.provenance
    assert "finance" in ans.provenance["routed_schemas"]
    assert "hr" not in ans.provenance["routed_schemas"]
