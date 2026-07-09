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
    # "total revenue" retrieves only {transaction}; rootbeerreview is 3 FK hops away
    # so it is outside the licensed scope (retrieval + 1-hop neighborhood) and L4
    # blocks it.
    class Rogue:
        def generate(self, question, retrieval, corpus, *, feedback=(), context=None):
            return GeneratedSql(
                sql="SELECT StarRating FROM rootbeerreview",
                tables_used=frozenset({"tbl_beer_factory_rootbeerreview"}),
            )

    ans = _ask(
        "total revenue", mem_gateway, corpus, settings, identity, sql_generator=Rogue()
    )
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["refused_by"] == "guardrail"
    assert ans.provenance["failed_layer"] == "term_semantics"


def test_flow_guardrail_blocks_write(mem_gateway, corpus, settings, identity):
    class Rogue:
        def generate(self, question, retrieval, corpus, *, feedback=(), context=None):
            return GeneratedSql(sql="DROP TABLE customers", tables_used=frozenset())

    ans = _ask("total revenue", mem_gateway, corpus, settings, identity, sql_generator=Rogue())
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["failed_layer"] == "policy_blacklist"


def test_flow_multitable_rogue_cannot_self_authorize_offscope_table(
    mem_gateway, corpus, settings, identity
):
    # The licensing scope is planned over retrieval (+ its FK join-neighborhood),
    # not the generator's declared tables, so declaring an in-scope table alongside
    # an off-scope one does not widen L4 to admit the off-scope table.
    #
    # "total revenue" retrieves only {transaction}; its 1-hop neighborhood is
    # {transaction, customers, rootbeer}. rootbeerreview is 3 FK hops away, so it is
    # genuinely out of scope even after the neighborhood widening and must still be
    # blocked at L4 - the SECURITY property the neighborhood change must preserve.
    class Rogue:
        def generate(self, question, retrieval, corpus, *, feedback=(), context=None):
            return GeneratedSql(
                sql="SELECT StarRating FROM rootbeerreview",
                tables_used=frozenset(
                    {"tbl_beer_factory_transaction", "tbl_beer_factory_rootbeerreview"}
                ),
            )

    ans = _ask("total revenue", mem_gateway, corpus, settings, identity, sql_generator=Rogue())
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["failed_layer"] == "term_semantics"


def test_flow_licenses_fk_neighbor_not_retrieved(corpus):
    # Decoupling L4 from retrieval recall: "total revenue" retrieves only the
    # transaction table, but its 1-hop FK neighbors (customers, rootbeer) are
    # licensed too, so an answer that legitimately needs one is not refused just
    # because the lexical retriever under-recalled.
    from governed_bi.graph import build_graph, plan_joins
    from governed_bi.server.context import assemble_context
    from governed_bi.server.flow import _licensed_table_ids

    graph = build_graph(corpus)
    retrieval = retrieve(corpus, "total revenue")
    assert set(retrieval.table_ids) == {"tbl_beer_factory_transaction"}  # retrieval missed the rest

    join_ids = plan_joins(graph, set(retrieval.table_ids)).join_ids
    licensed_ids = _licensed_table_ids(corpus, graph, retrieval, join_ids)
    # The guardrail's allowed_tables is the context's physical names, so check that.
    licensed = assemble_context(corpus, retrieval, licensed_table_ids=licensed_ids).allowed_table_names()

    assert "transaction" in licensed  # the retrieved table
    assert "customers" in licensed  # 1-hop FK neighbor retrieval never surfaced
    assert "rootbeer" in licensed  # 1-hop FK neighbor retrieval never surfaced
    assert "rootbeerreview" not in licensed  # 3 hops out: still not licensed


# --------------------------------------------------------------------------- #
# Flow: governed end-to-end (executes against the committed DB)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Self-repair loop (feedback -> regenerate -> re-guardrail -> re-execute)
# --------------------------------------------------------------------------- #


def test_flow_repairs_after_guardrail_rejection(bird_gateway, corpus, settings, identity):
    # First attempt references an out-of-scope table (blocked at L4); given that
    # feedback, the generator repairs to a valid in-scope query that executes.
    class RepairingGenerator:
        def generate(self, question, retrieval, corpus, *, feedback=(), context=None):
            if not feedback:
                # rootbeerreview is 3 FK hops from the retrieved transaction table,
                # so it is out of scope and blocked at L4.
                return GeneratedSql(
                    sql="SELECT StarRating FROM rootbeerreview",
                    tables_used=frozenset({"tbl_beer_factory_rootbeerreview"}),
                )
            return GeneratedSql(
                sql='SELECT SUM(PurchasePrice) AS total_revenue FROM "transaction"',
                tables_used=frozenset({"tbl_beer_factory_transaction"}),
                metric_id="metric_revenue",
            )

    ans = _ask("total revenue", bird_gateway, corpus, settings, identity, sql_generator=RepairingGenerator())
    assert ans.tier is ReliabilityTier.lineage  # repaired -> not governed
    assert "SUM(PurchasePrice)" in ans.sql
    assert ans.provenance["attempts"] == 2
    assert "repaired" in ans.provenance["uncertainty_flags"]


def test_flow_repair_exhaustion_fails_closed(mem_gateway, corpus, settings, identity):
    # Always produces a distinct but out-of-scope query; after MAX_REPAIR_ATTEMPTS
    # the flow gives up and refuses (never a confident wrong answer).
    from governed_bi.server.flow import MAX_REPAIR_ATTEMPTS

    class AlwaysBadGenerator:
        def __init__(self):
            self.n = 0

        def generate(self, question, retrieval, corpus, *, feedback=(), context=None):
            self.n += 1
            # Distinct each attempt (avoids the no-progress guard) but always the
            # off-scope rootbeerreview table, so every attempt is blocked at L4.
            return GeneratedSql(
                sql=f"SELECT StarRating AS c{self.n} FROM rootbeerreview",
                tables_used=frozenset({"tbl_beer_factory_rootbeerreview"}),
            )

    ans = _ask("total revenue", mem_gateway, corpus, settings, identity, sql_generator=AlwaysBadGenerator())
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["failed_layer"] == "term_semantics"
    assert ans.provenance["attempts"] == MAX_REPAIR_ATTEMPTS


def test_flow_no_progress_stops_early(mem_gateway, corpus, settings, identity):
    # A generator that ignores feedback and repeats the same bad SQL must not loop
    # to the cap; the no-progress guard stops it at two attempts. rootbeerreview is
    # off-scope for "total revenue" (3 FK hops from transaction), so it is blocked
    # at L4 on the first attempt and the repeat trips the no-progress guard.
    class StubbornGenerator:
        def generate(self, question, retrieval, corpus, *, feedback=(), context=None):
            return GeneratedSql(
                sql="SELECT StarRating FROM rootbeerreview",
                tables_used=frozenset({"tbl_beer_factory_rootbeerreview"}),
            )

    ans = _ask("total revenue", mem_gateway, corpus, settings, identity, sql_generator=StubbornGenerator())
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["attempts"] == 2


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
