"""D15 missing-edge refusal: cross-schema retrieval with no curated join.

Uses the same synthetic two-schema corpus pattern as
``test_multi_schema_guardrails`` (``orders`` in ``schema_a`` / ``schema_b``).
"""

from __future__ import annotations

from dataclasses import replace

from governed_bi.config import DataSourceConfig, Environment, Settings
from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import (
    Cardinality,
    Column,
    JoinAsset,
    LogicalType,
    Reliability,
    ReliabilityStatus,
    TableAsset,
)
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.graph import build_graph, detect_missing_join_path
from governed_bi.retrieval import RetrievalResult
from governed_bi.analyst.agent import answer_question_agent
from governed_bi.analyst.answer import ReliabilityTier

SCHEMA_A_ORDERS = "tbl_schema_a_orders"
SCHEMA_B_ORDERS = "tbl_schema_b_orders"


def _col(name: str, *, suspect: bool = False) -> Column:
    reliability = (
        Reliability(status=ReliabilityStatus.suspect, note="decoy")
        if suspect
        else Reliability()
    )
    return Column(
        physical_name=name,
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
        reliability=reliability,
    )


def _tables() -> tuple[TableAsset, TableAsset]:
    schema_a = TableAsset(
        id=SCHEMA_A_ORDERS,
        schema="schema_a",
        physical_name="orders",
        columns=[_col("order_id"), _col("amount")],
    )
    schema_b = TableAsset(
        id=SCHEMA_B_ORDERS,
        schema="schema_b",
        physical_name="orders",
        columns=[_col("order_id"), _col("amount", suspect=True)],
    )
    return schema_a, schema_b


def _cross_join() -> JoinAsset:
    return JoinAsset(
        id="join_schema_a_orders_schema_b_orders",
        left_table=SCHEMA_A_ORDERS,
        right_table=SCHEMA_B_ORDERS,
        on="schema_a.orders.order_id = schema_b.orders.order_id",
        cardinality=Cardinality.one_to_one,
        confidence=0.99,
    )


def _pg_settings() -> Settings:
    return replace(
        Settings.for_env(Environment.dev),
        datasource=DataSourceConfig(kind="postgres", dsn="host=x"),
    )


def test_detect_missing_join_path_cross_schema_no_join():
    a, b = _tables()
    corpus = Corpus(assets=[a, b])
    graph = build_graph(corpus)
    missing = detect_missing_join_path(
        corpus, graph, {SCHEMA_A_ORDERS, SCHEMA_B_ORDERS}, multi_schema=True
    )
    assert missing is not None
    assert missing.schemas == frozenset({"schema_a", "schema_b"})
    assert missing.table_ids == frozenset({SCHEMA_A_ORDERS, SCHEMA_B_ORDERS})


def test_detect_none_when_curated_cross_schema_join_exists():
    a, b = _tables()
    corpus = Corpus(assets=[a, b, _cross_join()])
    graph = build_graph(corpus)
    assert (
        detect_missing_join_path(
            corpus, graph, {SCHEMA_A_ORDERS, SCHEMA_B_ORDERS}, multi_schema=True
        )
        is None
    )


def test_detect_none_when_single_schema_mode():
    # SQLite / BIRD path: never missing-edge refuse (preserve byte-for-byte behavior).
    a, b = _tables()
    corpus = Corpus(assets=[a, b])
    graph = build_graph(corpus)
    assert (
        detect_missing_join_path(
            corpus, graph, {SCHEMA_A_ORDERS, SCHEMA_B_ORDERS}, multi_schema=False
        )
        is None
    )


def test_detect_none_when_tables_share_one_schema():
    a, _ = _tables()
    other = TableAsset(
        id="tbl_schema_a_items",
        schema="schema_a",
        physical_name="items",
        columns=[_col("item_id")],
    )
    corpus = Corpus(assets=[a, other])
    graph = build_graph(corpus)
    assert (
        detect_missing_join_path(
            corpus, graph, {SCHEMA_A_ORDERS, "tbl_schema_a_items"}, multi_schema=True
        )
        is None
    )


def test_agent_refuses_missing_edge(monkeypatch):
    # Missing-edge refusal fires in the agent rails' `assemble` node, before the
    # agent core runs — so it is deterministic and needs no live model (model=None
    # is never reached).
    a, b = _tables()
    corpus = Corpus(assets=[a, b]).for_analyst()
    settings = _pg_settings()
    assert settings.datasource.is_multi_schema()

    def _fake_retrieve(corpus_arg, question, *, embedder=None):
        return RetrievalResult(
            question=question,
            table_ids=[SCHEMA_A_ORDERS, SCHEMA_B_ORDERS],
            metric_ids=[],
            term_ids=[],
            few_shot_ids=[],
            scores={},
        )

    monkeypatch.setattr("governed_bi.analyst.agent.retrieve", _fake_retrieve)

    conn = SqliteConnector(":memory:")
    try:
        ans = answer_question_agent(
            "compare orders across schemas",
            Identity(user="dev", all_access=True),
            corpus=corpus,
            gateway=Gateway(conn),
            settings=settings,
            session_id="s",
            model=None,
        )
    finally:
        conn.close()

    assert ans.tier is ReliabilityTier.refused
    assert ans.sql is None
    assert ans.provenance["refused_by"] == "missing_edge"
    assert ans.provenance["schemas"] == ["schema_a", "schema_b"]
    assert ans.provenance["clarification_hint"]["kind"] == "missing_cross_schema_join"
    assert "cross-schema join" in (ans.escalation or "").lower()
