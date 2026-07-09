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

from governed_bi.gateway import Gateway, SqliteConnector  # noqa: E402
from governed_bi.curator.deep_agent import build_curator_agent, curator_tools  # noqa: E402

BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"


@pytest.fixture
def bird_connector():
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    yield conn
    conn.close()


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #


def test_tool_set_depends_on_gateway(bird_connector):
    assert len(curator_tools(bird_connector, "beer_factory")) == 1  # profile only
    gateway = Gateway(bird_connector)
    assert len(curator_tools(bird_connector, "beer_factory", gateway=gateway)) == 2  # + probe


def test_profile_facts_tool_reports_tables(bird_connector):
    profile_facts = curator_tools(bird_connector, "beer_factory")[0]
    facts = profile_facts()
    assert "transaction" in facts
    assert "customers" in facts


def test_run_probe_query_tool_is_readonly_and_returns_rows(bird_connector):
    gateway = Gateway(bird_connector)
    _profile, run_probe_query = curator_tools(bird_connector, "beer_factory", gateway=gateway)
    out = run_probe_query("SELECT COUNT(*) AS n FROM customers")
    assert "n" in out  # column header rendered

    # A write is rejected by the read-only gateway; the tool returns an error, never raises.
    bad = run_probe_query("DROP TABLE customers")
    assert bad.startswith("error:")


# --------------------------------------------------------------------------- #
# Agent construction (offline, fake model)
# --------------------------------------------------------------------------- #


def test_build_curator_agent_constructs_offline(bird_connector):
    gateway = Gateway(bird_connector)
    agent = build_curator_agent(
        FakeListChatModel(responses=["done"]),
        connector=bird_connector,
        db="beer_factory",
        gateway=gateway,
    )
    assert hasattr(agent, "invoke")
    nodes = set(agent.get_graph().nodes)
    assert "model" in nodes and "tools" in nodes
