"""Governance ledger: one record per governed run_query (pass and deny)."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn
from governed_bi.analyst.agent import answer_question_agent, build_agent_core
from governed_bi.analyst.answer import ReliabilityTier

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"
TXN = "tbl_beer_factory_transaction"


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


@pytest.fixture
def settings():
    return Settings.for_env(Environment.dev)


@pytest.fixture
def identity():
    return Identity(user="dev", all_access=True)


@pytest.fixture
def bird_gateway():
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    yield Gateway(conn)
    conn.close()


def test_ledger_records_pass_and_block(corpus, bird_gateway, settings, identity):
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT "StarRating" FROM "rootbeerreview"'},
            "c2",
        ),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
            "c3",
        ),
        AIMessage(content="42"),
    ]
    agent = build_agent_core(
        corpus,
        bird_gateway,
        identity,
        FakeToolModel(responses=turns),
        settings=settings,
        dialect="sqlite",
        default_schema="beer_factory",
    )
    final = agent.invoke({"messages": [HumanMessage("revenue")], "licensed": [], "ledger": []})
    ledger = final["ledger"]
    assert len([e for e in ledger if e.get("action") == "run_query"]) == 2
    assert ledger[0]["verdict"] == "block"
    assert ledger[1]["verdict"] == "pass"


def test_schema_qualified_sql_passes_and_executes_on_sqlite(
    corpus, bird_gateway, settings, identity
):
    # The engine is uniformly schema-qualified: a model may emit ``schema.table``,
    # and it must clear the guardrail AND execute against the SQLite fixture (the
    # connector ATTACHes the file under the ``beer_factory`` alias). Regression for
    # the multi_schema-flag removal.
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM beer_factory."transaction"'},
            "c2",
        ),
        AIMessage(content="done"),
    ]
    agent = build_agent_core(
        corpus,
        bird_gateway,
        identity,
        FakeToolModel(responses=turns),
        settings=settings,
        dialect="sqlite",
        default_schema="beer_factory",
    )
    final = agent.invoke({"messages": [HumanMessage("revenue")], "licensed": [], "ledger": []})
    run_queries = [e for e in final["ledger"] if e.get("action") == "run_query"]
    assert len(run_queries) == 1
    assert run_queries[0]["verdict"] == "pass"  # guardrail passed AND execution succeeded


def test_serving_schema_must_match_corpus(corpus, bird_gateway, settings, identity):
    # Config-drift guard: a pinned serving schema with no tables in the loaded corpus
    # would make the qualified allowlist never match, silently false-refusing every
    # query. build_serve_rails must reject it loudly (before any model runs).
    from dataclasses import replace

    mismatched = replace(
        settings, datasource=replace(settings.datasource, corpus_pin="restaurant", schema="restaurant")
    )
    with pytest.raises(ValueError, match="has no tables in the corpus"):
        answer_question_agent(
            "revenue",
            identity,
            corpus=corpus,  # schema=beer_factory, but serving schema pins restaurant
            gateway=bird_gateway,
            settings=mismatched,
            session_id="mismatch-test",
            model=FakeToolModel(responses=[AIMessage(content="unused")]),
        )


def test_ledger_on_answer_provenance(corpus, bird_gateway, settings, identity):
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
            "c2",
        ),
        AIMessage(content="done"),
    ]
    ans = answer_question_agent(
        "What is the total revenue?",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="ledger-test",
        model=FakeToolModel(responses=turns),
    )
    assert ans.tier is ReliabilityTier.governed
    assert ans.safety_clearance is True
    assert "governance_ledger" in ans.provenance
    assert ans.provenance["governance_ledger"][-1]["verdict"] == "pass"
    assert ans.provenance.get("runtime") == "agent"


def test_hard_stop_ledger_on_refusal(corpus, bird_gateway, settings, identity):
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn("run_query", {"sql": "DROP TABLE customers"}, "c2"),
    ]
    ans = answer_question_agent(
        "total revenue",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="l2-test",
        model=FakeToolModel(responses=turns),
    )
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["failed_layer"] == "policy_blacklist"
    assert ans.provenance["governance_ledger"][0]["layer"] == "policy_blacklist"
