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
        multi_schema=False,
        default_schema=None,
    )
    final = agent.invoke({"messages": [HumanMessage("revenue")], "licensed": [], "ledger": []})
    ledger = final["ledger"]
    assert len([e for e in ledger if e.get("action") == "run_query"]) == 2
    assert ledger[0]["verdict"] == "block"
    assert ledger[1]["verdict"] == "pass"


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
