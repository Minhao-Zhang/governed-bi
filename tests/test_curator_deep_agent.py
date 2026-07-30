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
    # bag → read_corpus + 7 writes; + probe → 9
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
        "annotate_columns",
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


# --------------------------------------------------------------------------- #
# annotate_columns — the batch write that stops the step budget scaling with
# schema width. One call per table instead of one per column; N tool calls in ONE
# assistant message cost a single `tools` super-step, N calls across N replies
# cost 3N. On a 703-column schema the per-column form alone exceeded the whole
# budget 23x.
# --------------------------------------------------------------------------- #


def _annotate_columns_tool(connector, bag):
    tools = curator_tools(connector, "beer_factory", bag=bag)
    return next(t for t in tools if t.__name__ == "annotate_columns")


def test_annotate_columns_writes_a_whole_table_in_one_call(bird_connector, bird_bag):
    table = next(iter(bird_bag.tables))
    cols = [c.physical_name for c in bird_bag.tables[table].columns][:3]
    tool = _annotate_columns_tool(bird_connector, bird_bag)

    out = tool(table, [{"column": c, "description": f"desc {c}"} for c in cols])
    assert len(out.splitlines()) == len(cols)
    assert all(line.startswith("ok:") for line in out.splitlines()), out
    written = {c.physical_name: c.description for c in bird_bag.tables[table].columns}
    for c in cols:
        assert written[c] == f"desc {c}"


def test_annotate_columns_carries_suspect_marks(bird_connector, bird_bag):
    from governed_bi.corpus.schemas import ReliabilityStatus

    table = next(iter(bird_bag.tables))
    col = bird_bag.tables[table].columns[0].physical_name
    tool = _annotate_columns_tool(bird_connector, bird_bag)
    tool(table, [{"column": col, "suspect": True, "note": "constant value"}])
    marked = bird_bag.tables[table].columns[0]
    assert marked.reliability.status is ReliabilityStatus.suspect
    # `annotate_column` prefixes the note ("DO NOT USE — ..."); the batch form must
    # go through it rather than reimplementing the write.
    assert "constant value" in marked.reliability.note


def test_annotate_columns_one_bad_spec_does_not_lose_the_batch(bird_connector, bird_bag):
    """A raising tool returns nothing, so the agent would lose every good
    annotation in the call and have to redo them — the churn this tool prevents."""
    table = next(iter(bird_bag.tables))
    good = bird_bag.tables[table].columns[0].physical_name
    tool = _annotate_columns_tool(bird_connector, bird_bag)

    out = tool(
        table,
        [
            {"column": good, "description": "kept"},
            {"description": "no column key"},
            {"column": "does_not_exist", "description": "unknown column"},
            "not even an object",
        ],
    )
    lines = out.splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("ok:")
    assert "missing required key" in lines[1]
    assert "error" in lines[2]
    assert "expected an object" in lines[3]
    # The good write survived its bad neighbours.
    assert bird_bag.tables[table].columns[0].description == "kept"


def test_annotate_columns_rejects_an_empty_batch(bird_connector, bird_bag):
    assert _annotate_columns_tool(bird_connector, bird_bag)(
        next(iter(bird_bag.tables)), []
    ).startswith("error:")
