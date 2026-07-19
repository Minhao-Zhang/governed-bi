"""Data-lake schema routing (D15): the ``schema_route_llm_pick`` wiring.

The pooled data-lake serve path (``eval.run_datalake``) turns on a single-schema
LLM pick so every question is scoped to one schema before retrieval. These tests
pin the ``assemble`` branch added for that: when ``schema_route_llm_pick`` is on
(and a model is present) the router calls ``select_schema`` and collapses
retrieval to the chosen schema; when it is off (the default), the multi-schema
shortlist + curated-join expansion path is unchanged.

Both run deterministically without a live model — the routing decision happens in
``assemble`` before the agent core, so a dummy/None model never has to answer.
"""

from __future__ import annotations

from dataclasses import replace

from governed_bi.analyst.agent import answer_question_agent
from governed_bi.config import DataSourceConfig, Environment, Settings
from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import Column, LogicalType, TableAsset
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.retrieval import RetrievalResult

SCHEMA_A_ORDERS = "tbl_schema_a_orders"
SCHEMA_B_ORDERS = "tbl_schema_b_orders"


def _col(name: str) -> Column:
    return Column(
        physical_name=name,
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
    )


def _two_schema_corpus() -> Corpus:
    a = TableAsset(
        id=SCHEMA_A_ORDERS,
        schema="schema_a",
        physical_name="orders",
        columns=[_col("order_id"), _col("amount")],
    )
    b = TableAsset(
        id=SCHEMA_B_ORDERS,
        schema="schema_b",
        physical_name="orders",
        columns=[_col("order_id"), _col("amount")],
    )
    return Corpus(assets=[a, b]).for_analyst()


def _pg_settings(**over) -> Settings:
    base = replace(
        Settings.for_env(Environment.dev),
        datasource=DataSourceConfig(kind="postgres", dsn="host=x"),  # schema=None: span all
    )
    return replace(base, **over) if over else base


def test_llm_pick_calls_select_schema_and_collapses_retrieval(monkeypatch):
    """With ``schema_route_llm_pick`` on and a model present, ``assemble`` picks one
    schema via ``select_schema`` and retrieval only ever sees that schema."""
    corpus = _two_schema_corpus()
    settings = _pg_settings(schema_route_llm_pick=True, schema_route_top_k=8)

    seen: dict = {}

    def _spy_select(corpus_arg, question, candidates, *, chat, **kw):
        seen["candidates"] = sorted(candidates)
        return "schema_a"

    def _fake_retrieve(corpus_arg, question, *, embedder=None):
        seen["retrieval_schemas"] = sorted(
            {t.schema for t in corpus_arg.assets if isinstance(t, TableAsset)}
        )
        return RetrievalResult(
            question=question,
            table_ids=[SCHEMA_A_ORDERS],
            metric_ids=[],
            term_ids=[],
            few_shot_ids=[],
            scores={},
        )

    monkeypatch.setattr("governed_bi.analyst.agent.select_schema", _spy_select)
    monkeypatch.setattr("governed_bi.analyst.agent.retrieve", _fake_retrieve)

    conn = SqliteConnector(":memory:")
    try:
        # A truthy model makes ``build_serve_rails`` construct the router chat (the
        # guard is ``model is not None``); ``select_schema`` is spied so the object
        # is never actually called. The agent core past ``assemble`` will fail on
        # this dummy model — irrelevant, the routing decision already happened.
        try:
            answer_question_agent(
                "total order amount",
                Identity(user="dev", all_access=True),
                corpus=corpus,
                gateway=Gateway(conn),
                settings=settings,
                session_id="s",
                model=object(),
            )
        except Exception:
            pass
    finally:
        conn.close()

    assert seen.get("candidates") == ["schema_a", "schema_b"]  # shortlist offered both
    assert seen.get("retrieval_schemas") == ["schema_a"]  # collapsed to the pick


def test_default_path_does_not_pick_and_refuses_missing_edge(monkeypatch):
    """Default (``schema_route_llm_pick`` off): no ``select_schema`` call; a
    two-schema question with no curated join still refuses on missing edge."""
    corpus = _two_schema_corpus()
    settings = _pg_settings()  # llm_pick defaults to False
    assert settings.schema_route_llm_pick is False

    called = {"select": False}

    def _spy_select(*a, **k):
        called["select"] = True
        return "schema_a"

    def _fake_retrieve(corpus_arg, question, *, embedder=None):
        return RetrievalResult(
            question=question,
            table_ids=[SCHEMA_A_ORDERS, SCHEMA_B_ORDERS],
            metric_ids=[],
            term_ids=[],
            few_shot_ids=[],
            scores={},
        )

    monkeypatch.setattr("governed_bi.analyst.agent.select_schema", _spy_select)
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
            model=None,  # never reached: assemble refuses on the missing edge first
        )
    finally:
        conn.close()

    assert called["select"] is False  # single-schema pick is off by default
    assert ans.provenance["refused_by"] == "missing_edge"
