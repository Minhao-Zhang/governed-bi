"""Tool scoping: excluded never surfaces; licensing grows only via inspect_schema."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.corpus.schemas import TableAsset
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn
from governed_bi.analyst.agent import build_agent_core
from governed_bi.analyst.tools import make_tools, render_retrieval
from governed_bi.retrieval import retrieve

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"
TXN = "tbl_beer_factory_transaction"


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


@pytest.fixture
def corpus_full():
    return load_corpus(CORPUS_ROOT, schema="beer_factory")


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


def test_search_corpus_skips_excluded(corpus_full, bird_gateway, identity):
    excluded_ids = {
        a.id
        for a in corpus_full.assets
        if isinstance(a, TableAsset) and a.governance.excluded
    }
    tools = {t.name: t for t in make_tools(corpus_full.for_analyst(), bird_gateway, identity)}
    out = tools["search_corpus"].invoke({"query": "transaction revenue"})
    for eid in excluded_ids:
        assert eid not in out


def test_inspect_schema_rejects_unknown_and_licenses(corpus, bird_gateway, settings, identity):
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": "tbl_does_not_exist"}, "c1"),
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c2"),
        AIMessage(content="ok"),
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
    final = agent.invoke({"messages": [HumanMessage("x")], "licensed": [], "ledger": []})
    assert TXN in final["licensed"]
    texts = " ".join(str(getattr(m, "content", "")) for m in final["messages"])
    assert "not available" in texts
    assert "PurchasePrice" in texts or "physical:" in texts


def test_sample_rows_requires_license(corpus, bird_gateway, settings, identity):
    turns = [
        ai_tool_turn("sample_rows", {"table_id": TXN, "n": 2}, "c1"),
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c2"),
        ai_tool_turn("sample_rows", {"table_id": TXN, "n": 2}, "c3"),
        AIMessage(content="ok"),
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
    final = agent.invoke({"messages": [HumanMessage("x")], "licensed": [], "ledger": []})
    texts = [str(getattr(m, "content", "")) for m in final["messages"]]
    assert any("not licensed" in t for t in texts)
    sample_passes = [
        e for e in final["ledger"] if e.get("action") == "sample_rows" and e.get("verdict") == "pass"
    ]
    assert sample_passes
    assert "SELECT *" not in sample_passes[0]["sql"]


def test_render_retrieval_lists_tables(corpus):
    r = retrieve(corpus, "total revenue")
    text = render_retrieval(r)
    assert "tables:" in text or "metrics:" in text
