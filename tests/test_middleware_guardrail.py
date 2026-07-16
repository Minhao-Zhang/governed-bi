"""GovernanceMiddleware: pass / block / L2 hard-stop / attempt cap."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn
from governed_bi.analyst.agent import build_agent_core
from governed_bi.analyst.middleware import GovernanceHardStop

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


def _agent(corpus, gateway, identity, settings, responses):
    return build_agent_core(
        corpus,
        gateway,
        identity,
        FakeToolModel(responses=responses),
        settings=settings,
        dialect="sqlite",
        multi_schema=False,
        default_schema=None,
    )


def test_middleware_pass_and_ledger(corpus, bird_gateway, settings, identity):
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
            "c2",
        ),
        AIMessage(content="done"),
    ]
    agent = _agent(corpus, bird_gateway, identity, settings, turns)
    final = agent.invoke({"messages": [HumanMessage("revenue")], "licensed": [], "ledger": []})
    assert TXN in final["licensed"]
    assert final["ledger"][-1]["verdict"] == "pass"
    assert final["ledger"][-1]["action"] == "run_query"


def test_middleware_blocks_off_scope_table(corpus, bird_gateway, settings, identity):
    # Inspect transaction only; query an unlicensed table → L4 block (coachable).
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT "StarRating" FROM "rootbeerreview"'},
            "c2",
        ),
        AIMessage(content="gave up"),
    ]
    agent = _agent(corpus, bird_gateway, identity, settings, turns)
    final = agent.invoke({"messages": [HumanMessage("x")], "licensed": [], "ledger": []})
    blocked = [e for e in final["ledger"] if e.get("verdict") == "block"]
    assert blocked
    assert blocked[0]["layer"] == "term_semantics"
    texts = " ".join(str(getattr(m, "content", "")) for m in final["messages"])
    assert "BLOCKED" in texts


def test_middleware_l2_hard_stop(corpus, bird_gateway, settings, identity):
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn("run_query", {"sql": "DROP TABLE customers"}, "c2"),
    ]
    agent = _agent(corpus, bird_gateway, identity, settings, turns)
    with pytest.raises(GovernanceHardStop) as ei:
        agent.invoke({"messages": [HumanMessage("x")], "licensed": [], "ledger": []})
    assert ei.value.entry["layer"] == "policy_blacklist"
    assert ei.value.entry["verdict"] == "block"


def test_middleware_attempt_cap(corpus, bird_gateway, settings, identity):
    # Four run_query attempts: first three blocked (off-scope), fourth hits cap.
    bad = {"sql": 'SELECT "StarRating" FROM "rootbeerreview"'}
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c0"),
        ai_tool_turn("run_query", bad, "c1"),
        ai_tool_turn("run_query", bad, "c2"),
        ai_tool_turn("run_query", bad, "c3"),
        ai_tool_turn("run_query", bad, "c4"),
        AIMessage(content="stop"),
    ]
    agent = _agent(corpus, bird_gateway, identity, settings, turns)
    final = agent.invoke({"messages": [HumanMessage("x")], "licensed": [], "ledger": []})
    run_entries = [e for e in final["ledger"] if e.get("action") == "run_query"]
    assert any(e.get("verdict") == "cap" for e in run_entries)
    assert sum(1 for e in run_entries if e.get("verdict") == "block") == 3
