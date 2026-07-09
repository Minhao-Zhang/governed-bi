"""Tests for the LLM-backed SQL generator (server.sqlgen.LlmSqlGenerator).

No network: the ChatClient is a scripted StaticChatClient. The end-to-end cases
run the whole serve flow with that fake model so a *real* generator path (prompt
-> SQL -> guardrails -> execute -> stamp) is exercised, executing against the
committed beer_factory DB (skipped if absent).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.graph import build_graph, plan_joins
from governed_bi.llm import StaticChatClient
from governed_bi.retrieval import retrieve
from governed_bi.server import LlmSqlGenerator, answer_question
from governed_bi.server.answer import ReliabilityTier
from governed_bi.server.context import assemble_context
from governed_bi.server.flow import _licensed_table_ids
from governed_bi.server.sqlgen import RepairFeedback

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"

REVENUE_SQL = 'SELECT SUM(PurchasePrice) AS total_revenue FROM "transaction"'


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, db="beer_factory").for_server()


@pytest.fixture
def settings():
    return Settings.for_env(Environment.dev)


@pytest.fixture
def identity():
    return Identity(user="dev", all_access=True)


@pytest.fixture
def mem_gateway():
    conn = SqliteConnector(":memory:")
    yield Gateway(conn)
    conn.close()


@pytest.fixture
def bird_gateway():
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    yield Gateway(conn)
    conn.close()


def _flow_context(corpus, question):
    graph = build_graph(corpus)
    retrieval = retrieve(corpus, question)
    try:
        join_ids = plan_joins(graph, set(retrieval.table_ids)).join_ids
    except ValueError:
        join_ids = []
    licensed_ids = _licensed_table_ids(corpus, graph, retrieval, join_ids)
    ctx = assemble_context(corpus, retrieval, licensed_table_ids=licensed_ids)
    return ctx, retrieval


# --------------------------------------------------------------------------- #
# Unit: prompt assembly, extraction, decline, feedback
# --------------------------------------------------------------------------- #


def test_prompt_carries_rules_context_and_question(corpus):
    ctx, retrieval = _flow_context(corpus, "total revenue")
    chat = StaticChatClient(REVENUE_SQL)
    gen = LlmSqlGenerator(chat, dialect="sqlite")

    gen.generate("total revenue", retrieval, corpus, context=ctx)

    system, user = chat.calls[0]
    assert "read-only SELECT" in system
    assert "DO NOT USE" in system
    assert "sqlite" in system
    assert "transaction" in user  # the rendered schema
    assert "Question: total revenue" in user


def test_returns_generated_sql_with_mapped_tables(corpus):
    ctx, retrieval = _flow_context(corpus, "total revenue")
    gen = LlmSqlGenerator(StaticChatClient(REVENUE_SQL), dialect="sqlite")

    out = gen.generate("total revenue", retrieval, corpus, context=ctx)

    assert out is not None
    assert out.sql == REVENUE_SQL
    assert out.tables_used == frozenset({"tbl_beer_factory_transaction"})


def test_strips_markdown_fences(corpus):
    ctx, retrieval = _flow_context(corpus, "total revenue")
    fenced = f"```sql\n{REVENUE_SQL};\n```"
    gen = LlmSqlGenerator(StaticChatClient(fenced), dialect="sqlite")

    out = gen.generate("total revenue", retrieval, corpus, context=ctx)
    assert out is not None
    assert out.sql == REVENUE_SQL  # fence + trailing semicolon removed


def test_declines_on_sentinel(corpus):
    ctx, retrieval = _flow_context(corpus, "total revenue")
    gen = LlmSqlGenerator(StaticChatClient("CANNOT_ANSWER"), dialect="sqlite")
    assert gen.generate("weather on mars", retrieval, corpus, context=ctx) is None


def test_sql_containing_the_sentinel_literal_is_not_a_decline(corpus):
    # Valid SQL that merely contains the literal 'CANNOT_ANSWER' (e.g. a status
    # filter) must NOT be mistaken for a decline.
    ctx, retrieval = _flow_context(corpus, "total revenue")
    sql = "SELECT COUNT(*) AS n FROM \"transaction\" WHERE PaymentMethod = 'CANNOT_ANSWER'"
    out = LlmSqlGenerator(StaticChatClient(sql), dialect="sqlite").generate(
        "count", retrieval, corpus, context=ctx
    )
    assert out is not None
    assert out.sql == sql


def test_extract_takes_last_block_and_drops_language_tag(corpus):
    ctx, retrieval = _flow_context(corpus, "total revenue")
    # An echoed example block, then the real answer in a python-tagged fence.
    response = (
        "```sql\nSELECT wrong FROM x\n```\n"
        f"Answer:\n```python\n{REVENUE_SQL}\n```"
    )
    out = LlmSqlGenerator(StaticChatClient(response), dialect="sqlite").generate(
        "total revenue", retrieval, corpus, context=ctx
    )
    assert out is not None
    assert out.sql == REVENUE_SQL  # last block, and 'python' tag not captured


def test_declines_on_empty_response(corpus):
    ctx, retrieval = _flow_context(corpus, "total revenue")
    gen = LlmSqlGenerator(StaticChatClient("   "), dialect="sqlite")
    assert gen.generate("total revenue", retrieval, corpus, context=ctx) is None


def test_feedback_is_threaded_into_the_prompt(corpus):
    ctx, retrieval = _flow_context(corpus, "total revenue")
    chat = StaticChatClient(REVENUE_SQL)
    gen = LlmSqlGenerator(chat, dialect="sqlite")

    fb = (RepairFeedback(sql="SELECT bad FROM nope", stage="guardrail", reason="term_semantics: off scope"),)
    gen.generate("total revenue", retrieval, corpus, feedback=fb, context=ctx)

    _system, user = chat.calls[0]
    assert "previous attempt" in user.lower()
    assert "SELECT bad FROM nope" in user
    assert "off scope" in user


def test_unparseable_sql_yields_empty_tables_used(corpus):
    ctx, retrieval = _flow_context(corpus, "total revenue")
    gen = LlmSqlGenerator(StaticChatClient("SELECT ((("), dialect="sqlite")
    out = gen.generate("total revenue", retrieval, corpus, context=ctx)
    # Still returned (guardrail L1 will reject it); tables_used degrades to empty.
    assert out is not None
    assert out.tables_used == frozenset()


def test_context_fallback_when_flow_did_not_pass_one(corpus):
    # Without a flow-built context, it builds one from the retrieved tables.
    retrieval = retrieve(corpus, "total revenue")
    gen = LlmSqlGenerator(StaticChatClient(REVENUE_SQL), dialect="sqlite")
    out = gen.generate("total revenue", retrieval, corpus, context=None)
    assert out is not None
    assert out.sql == REVENUE_SQL


# --------------------------------------------------------------------------- #
# End-to-end through the serve flow (fake model, real guardrails + execution)
# --------------------------------------------------------------------------- #


def _ask(question, gateway, corpus, settings, identity, **kw):
    return answer_question(
        question, identity, corpus=corpus, gateway=gateway, settings=settings, session_id="s", **kw
    )


def test_flow_governed_with_llm_generator(bird_gateway, corpus, settings, identity):
    gen = LlmSqlGenerator(StaticChatClient(REVENUE_SQL), dialect="sqlite")
    ans = _ask("total revenue", bird_gateway, corpus, settings, identity, sql_generator=gen)
    assert ans.tier is ReliabilityTier.governed  # clean single-shot -> governed
    assert "SUM(PurchasePrice)" in ans.sql
    assert ans.provenance["attempts"] == 1


def test_flow_llm_repairs_after_guardrail_rejection(bird_gateway, corpus, settings, identity):
    # First reply is an off-scope table (L4 blocks); second is valid and executes.
    chat = StaticChatClient(["SELECT StarRating FROM rootbeerreview", REVENUE_SQL])
    gen = LlmSqlGenerator(chat, dialect="sqlite")
    ans = _ask("total revenue", bird_gateway, corpus, settings, identity, sql_generator=gen)

    assert ans.tier is ReliabilityTier.lineage  # repaired -> not governed
    assert "SUM(PurchasePrice)" in ans.sql
    assert ans.provenance["attempts"] == 2
    assert "repaired" in ans.provenance["uncertainty_flags"]
    # The repair prompt carried the guardrail feedback.
    assert any("term_semantics" in user for _system, user in chat.calls[1:])


def test_flow_llm_decline_fails_closed(mem_gateway, corpus, settings, identity):
    gen = LlmSqlGenerator(StaticChatClient("CANNOT_ANSWER"), dialect="sqlite")
    ans = _ask("total revenue", mem_gateway, corpus, settings, identity, sql_generator=gen)
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["refused_by"] == "no_coverage"
