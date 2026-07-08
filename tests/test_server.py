"""Tests for the serve flow: routing, term binding, SQL gen, guardrails, stamp.

Logic tests run on an in-memory SQLite gateway; the governed end-to-end cases
execute against the committed beer_factory database (skipped if absent).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.retrieval import RetrievalResult, retrieve
from governed_bi.server import (
    Route,
    TemplateSqlGenerator,
    answer_question,
    bind_terms,
    route_intent,
)
from governed_bi.server.answer import ReliabilityTier, UncertaintySignals, reliability_tier
from governed_bi.server.sqlgen import GeneratedSql

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"


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


# --------------------------------------------------------------------------- #
# Routing + term binding
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What is revenue?", Route.knowledge_qa),
        ("Show the revenue trend over time", Route.deep_analysis),
        ("How many customers are there?", Route.kpi_lookup),
        ("Revenue by brand for premium labels", Route.nl2sql),
    ],
)
def test_route_intent(question, expected):
    assert route_intent(question) == expected


def test_bind_terms(corpus):
    bound = bind_terms(corpus, "total revenue by brand")
    assert "term_revenue" in bound
    assert "term_brand" in bound


def test_bind_terms_no_false_fire(corpus):
    # "brandish" contains "brand" as a substring but not as a token.
    assert bind_terms(corpus, "the knight brandished a sword") == []


# --------------------------------------------------------------------------- #
# Template SQL generator
# --------------------------------------------------------------------------- #


def test_template_generator_emits_metric_sql(corpus):
    gen = TemplateSqlGenerator().generate("total revenue", retrieve(corpus, "total revenue"), corpus)
    assert gen is not None
    assert "SUM(PurchasePrice)" in gen.sql
    assert gen.tables_used == frozenset({"tbl_beer_factory_transaction"})
    assert gen.metric_id == "metric_revenue"


def test_template_generator_declines_without_metric(corpus):
    empty = RetrievalResult(question="x")  # no metric_ids
    assert TemplateSqlGenerator().generate("x", empty, corpus) is None


# --------------------------------------------------------------------------- #
# Reliability stamp
# --------------------------------------------------------------------------- #


def test_reliability_tier_clean_is_governed():
    assert reliability_tier(UncertaintySignals()) is ReliabilityTier.governed


def test_reliability_tier_low_confidence_join_is_lineage():
    assert reliability_tier(UncertaintySignals(low_confidence_join=True)) is ReliabilityTier.lineage


def test_reliability_tier_fenced_raw():
    assert reliability_tier(UncertaintySignals(fenced_raw_fallback=True)) is ReliabilityTier.fenced_raw


# --------------------------------------------------------------------------- #
# Flow: fail-closed paths (no execution needed)
# --------------------------------------------------------------------------- #


def _ask(question, gateway, corpus, settings, identity, **kw):
    return answer_question(
        question, identity, corpus=corpus, gateway=gateway, settings=settings, session_id="s", **kw
    )


def test_flow_refuse_gate(mem_gateway, corpus, settings, identity):
    ans = _ask("How many employees work at the factory?", mem_gateway, corpus, settings, identity)
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["refused_by"] == "refuse_gate"
    assert ans.escalation  # the curated negative-example escalation blob
    assert ans.sql is None


def test_flow_no_coverage_refuses(mem_gateway, corpus, settings, identity):
    ans = _ask("Tell me about the weather on Mars", mem_gateway, corpus, settings, identity)
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["refused_by"] == "no_coverage"


def test_flow_guardrail_blocks_out_of_scope_table(mem_gateway, corpus, settings, identity):
    # A rogue generator emits a table retrieval never surfaced for this question.
    class Rogue:
        def generate(self, question, retrieval, corpus):
            return GeneratedSql(
                sql="SELECT First FROM customers",
                tables_used=frozenset({"tbl_beer_factory_customers"}),
            )

    ans = _ask(
        "What is the average star rating?", mem_gateway, corpus, settings, identity, sql_generator=Rogue()
    )
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["refused_by"] == "guardrail"
    assert ans.provenance["failed_layer"] == "term_semantics"


def test_flow_guardrail_blocks_write(mem_gateway, corpus, settings, identity):
    class Rogue:
        def generate(self, question, retrieval, corpus):
            return GeneratedSql(sql="DROP TABLE customers", tables_used=frozenset())

    ans = _ask("total revenue", mem_gateway, corpus, settings, identity, sql_generator=Rogue())
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["failed_layer"] == "policy_blacklist"


# --------------------------------------------------------------------------- #
# Flow: governed end-to-end (executes against the committed DB)
# --------------------------------------------------------------------------- #


def test_flow_governed_revenue(bird_gateway, corpus, settings, identity):
    ans = _ask("What is the total revenue?", bird_gateway, corpus, settings, identity)
    assert ans.tier is ReliabilityTier.governed
    assert "SUM(PurchasePrice)" in ans.sql
    assert "total_revenue" in ans.text  # single-cell numeric answer
    assert ans.provenance["metric_id"] == "metric_revenue"


def test_flow_governed_avg_rating(bird_gateway, corpus, settings, identity):
    ans = _ask("What is the average star rating?", bird_gateway, corpus, settings, identity)
    assert ans.tier is ReliabilityTier.governed
    assert "AVG(StarRating)" in ans.sql
    assert ans.provenance["min_join_confidence"] == 1.0  # single-table, no joins
