"""Tool scoping: excluded never surfaces; licensing grows only via inspect_schema."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from governed_bi.analyst.agent import build_agent_core
from governed_bi.analyst.tools import make_tools, render_retrieval
from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.corpus.schemas import TableAsset
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn
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
        default_schema="beer_factory",
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
        default_schema="beer_factory",
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


# --------------------------------------------------------------------------- #
# AUDIT S4: inspect_schema wrote straight into `licensed`, which becomes L4's
# allowed_tables — so the agent grew its own authorisation set.
# --------------------------------------------------------------------------- #


def _two_schema_corpus():
    from governed_bi.corpus import Corpus
    from governed_bi.corpus.schemas import Column, LogicalType, TableAsset

    def table(schema, name):
        return TableAsset(
            id=f"tbl_{schema}_{name}",
            schema=schema,
            physical_name=name,
            columns=[
                Column(
                    physical_name="id",
                    physical_type="INTEGER",
                    logical_type=LogicalType.integer,
                    nullable=False,
                    is_unique=True,
                )
            ],
        )

    return Corpus(assets=[table("sales", "orders"), table("hr", "salaries")])


def _inspect(tools, table_id):
    tool = next(t for t in tools if t.name == "inspect_schema")
    return tool.invoke(
        {
            "name": "inspect_schema",
            "args": {"table_id": table_id},
            "id": "c1",
            "type": "tool_call",
        }
    )


def test_inspect_schema_refuses_a_table_outside_the_routed_schemas():
    from governed_bi.analyst.tools import make_tools

    corpus = _two_schema_corpus()
    tools = make_tools(corpus, None, None, licensable_schemas={"sales"})

    routed = _inspect(tools, "tbl_sales_orders")
    assert routed.update.get("licensed") == ["tbl_sales_orders"]

    off_route = _inspect(tools, "tbl_hr_salaries")
    # No license granted, and the refusal does not confirm the table exists.
    assert "licensed" not in off_route.update
    assert "not available" in off_route.update["messages"][0].content


def test_unbounded_scope_preserves_single_schema_behaviour():
    """A single-schema corpus (BIRD / demo / eval) routes nothing; nothing changes."""
    from governed_bi.analyst.tools import make_tools

    corpus = _two_schema_corpus()
    tools = make_tools(corpus, None, None, licensable_schemas=None)
    assert _inspect(tools, "tbl_hr_salaries").update.get("licensed") == ["tbl_hr_salaries"]


def test_search_corpus_does_not_advertise_out_of_scope_tables():
    from governed_bi.analyst.tools import make_tools

    corpus = _two_schema_corpus()
    tools = make_tools(corpus, None, None, licensable_schemas={"sales"})
    search = next(t for t in tools if t.name == "search_corpus")
    out = search.invoke({"query": "salaries orders"})
    assert "tbl_hr_salaries" not in out


# --------------------------------------------------------------------------- #
# AUDIT T1: the sample_rows inner PII filter (excluded + allowlist) had never
# executed, because every test passed corpus.for_analyst(), which strips upstream.
# Drive it with an UNFILTERED corpus so the inner guard is the only thing standing.
# --------------------------------------------------------------------------- #


def _corpus_with_an_excluded_and_a_suspect_column():
    from governed_bi.corpus import Corpus
    from governed_bi.corpus.schemas import (
        Column,
        Governance,
        LogicalType,
        Reliability,
        ReliabilityStatus,
        TableAsset,
    )

    def col(name, *, excluded=False, suspect=False):
        return Column(
            physical_name=name,
            physical_type="TEXT",
            logical_type=LogicalType.string,
            nullable=True,
            is_unique=False,
            governance=Governance(excluded=excluded),
            reliability=Reliability(
                status=ReliabilityStatus.suspect if suspect else ReliabilityStatus.ok
            ),
        )

    table = TableAsset(
        id="tbl_demo_people",
        schema="demo",
        physical_name="people",
        columns=[col("name"), col("ssn", excluded=True), col("decoy", suspect=True)],
    )
    return Corpus(assets=[table])


def _middleware(corpus):
    from dataclasses import replace as _replace

    from governed_bi.analyst.middleware import GovernanceMiddleware
    from governed_bi.config import Environment, Settings

    settings = _replace(Settings.for_env(Environment.dev), hard_block_suspect_columns=True)
    return GovernanceMiddleware(
        corpus,
        gateway=None,
        identity=None,
        dialect="sqlite",
        default_schema="demo",
        settings=settings,
    )


def test_sample_rows_excludes_excluded_and_suspect_columns_on_a_raw_corpus():
    """Inv #2 defence in depth: the guard holds even with nothing stripped upstream."""
    mw = _middleware(_corpus_with_an_excluded_and_a_suspect_column())

    sql, err = mw._sample_sql({"table_id": "tbl_demo_people", "n": 3}, ["tbl_demo_people"])

    assert err is None, err
    assert "name" in sql
    assert "ssn" not in sql, "an excluded column reached a sample SELECT"
    assert "decoy" not in sql, "a suspect column reached a sample SELECT"


def test_sample_rows_refuses_when_nothing_is_allowlisted():
    from governed_bi.corpus import Corpus
    from governed_bi.corpus.schemas import Column, Governance, LogicalType, TableAsset

    table = TableAsset(
        id="tbl_demo_secrets",
        schema="demo",
        physical_name="secrets",
        columns=[
            Column(
                physical_name="token",
                physical_type="TEXT",
                logical_type=LogicalType.string,
                nullable=True,
                is_unique=False,
                governance=Governance(excluded=True),
            )
        ],
    )
    mw = _middleware(Corpus(assets=[table]))

    sql, err = mw._sample_sql({"table_id": "tbl_demo_secrets", "n": 3}, ["tbl_demo_secrets"])
    assert sql is None
    assert "no allowlisted columns" in err


def test_sample_rows_still_requires_a_license():
    mw = _middleware(_corpus_with_an_excluded_and_a_suspect_column())
    sql, err = mw._sample_sql({"table_id": "tbl_demo_people", "n": 3}, [])
    assert sql is None
    assert "not licensed" in err
