"""Tests for the deepagents curator harness (curator.deep_agent).

The tools are real (they profile / probe the committed beer_factory DB); the deep
agent is constructed with a fake LangChain model so construction is verified
offline. Running the autonomous loop needs a live model and is not exercised here.
Skipped if the ``agents`` extra (deepagents) is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("deepagents")

from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402

from governed_bi.curator.asset_bag import AssetBag  # noqa: E402
from governed_bi.curator.deep_agent import build_curator_agent, curator_tools  # noqa: E402
from governed_bi.curator.profile import profile_database  # noqa: E402
from governed_bi.gateway import Gateway, SqliteConnector  # noqa: E402

BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"


@pytest.fixture
def bird_connector():
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    yield conn
    conn.close()


@pytest.fixture
def bird_bag(bird_connector):
    tables = profile_database(bird_connector, schema="beer_factory")
    return AssetBag.from_tables("beer_factory", tables)


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #


def test_tool_set_depends_on_gateway_and_bag(bird_connector, bird_bag):
    # Without bag/gateway: read_corpus stub only.
    assert len(curator_tools(bird_connector, "beer_factory")) == 1
    gateway = Gateway(bird_connector)
    assert len(curator_tools(bird_connector, "beer_factory", gateway=gateway)) == 2
    # bag → read_corpus + 6 writes; + probe → 8
    tools = curator_tools(
        bird_connector, "beer_factory", gateway=gateway, bag=bird_bag
    )
    names = [t.__name__ for t in tools]
    assert names == [
        "read_corpus",
        "run_probe_query",
        "upsert_join",
        "upsert_metric",
        "upsert_term",
        "upsert_few_shot",
        "annotate_table",
        "annotate_column",
    ]


def test_read_corpus_tool_reports_tables(bird_connector, bird_bag):
    read_corpus = curator_tools(bird_connector, "beer_factory", bag=bird_bag)[0]
    facts = read_corpus()
    assert "transaction" in facts
    assert "customers" in facts
    filtered = read_corpus(table="customers")
    assert "customers" in filtered
    assert "transaction" not in filtered or "transaction" in bird_bag.tables


def test_run_probe_query_tool_is_readonly_and_returns_rows(bird_connector, bird_bag):
    gateway = Gateway(bird_connector)
    tools = curator_tools(
        bird_connector, "beer_factory", gateway=gateway, bag=bird_bag
    )
    by_name = {t.__name__: t for t in tools}
    out = by_name["run_probe_query"]("SELECT COUNT(*) AS n FROM customers")
    assert "n" in out

    bad = by_name["run_probe_query"]("DROP TABLE customers")
    assert bad.startswith("error:")


def test_upsert_join_rejects_unknown_table(bird_bag):
    msg = bird_bag.upsert_join("nope", "customers", "nope.id = customers.id")
    assert msg.startswith("error:")


def test_annotate_column_validation_reject(bird_bag):
    table = next(iter(bird_bag.tables))
    col = bird_bag.tables[table].columns[0].physical_name
    msg = bird_bag.annotate_column(table, col, role="not_a_role")
    assert msg.startswith("error:")
    msg2 = bird_bag.annotate_column(table, col, description="ok desc")
    assert msg2.startswith("ok:")


def test_build_curator_agent_constructs_with_filesystem_backend(
    bird_connector, bird_bag, tmp_path
):
    gateway = Gateway(bird_connector)
    agent = build_curator_agent(
        FakeListChatModel(responses=["done"]),
        connector=bird_connector,
        schema="beer_factory",
        gateway=gateway,
        bag=bird_bag,
        run_dir=tmp_path,
    )
    assert hasattr(agent, "invoke")
    nodes = set(agent.get_graph().nodes)
    assert "model" in nodes and "tools" in nodes
