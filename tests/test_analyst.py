"""Tests for the shared serve substrate: routing, term binding, the reliability
stamp, and L4 licensing scope.

These exercise modules both the (removed) deterministic flow and the agentic
serve core share. End-to-end serve behavior (fail-closed paths, self-repair,
governed answers) is asserted on the agent path in test_agent_governance_fixes.py
and test_governance_invariants.py; the live end-to-end turn lives in
scripts/live_smoke.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.retrieval import retrieve
from governed_bi.analyst import Route, bind_terms, route_intent
from governed_bi.analyst.answer import (
    ReliabilityTier,
    SemanticAssurance,
    UncertaintySignals,
    reliability_tier,
    semantic_assurance,
)

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"


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
def mem_gateway():
    conn = SqliteConnector(":memory:")
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
# Reliability stamp
# --------------------------------------------------------------------------- #


def test_reliability_tier_clean_is_governed():
    assert reliability_tier(UncertaintySignals()) is ReliabilityTier.governed


def test_reliability_tier_low_confidence_join_is_lineage():
    assert reliability_tier(UncertaintySignals(low_confidence_join=True)) is ReliabilityTier.lineage


def test_reliability_tier_fenced_raw():
    assert reliability_tier(UncertaintySignals(fenced_raw_fallback=True)) is ReliabilityTier.fenced_raw


def test_semantic_assurance_axis():
    # The epistemic axis, distinct from safety, that the tier projects.
    assert semantic_assurance(UncertaintySignals()) is SemanticAssurance.grounded
    assert semantic_assurance(UncertaintySignals(repaired=True)) is SemanticAssurance.heuristic
    assert (
        semantic_assurance(UncertaintySignals(fenced_raw_fallback=True))
        is SemanticAssurance.unverified
    )


# --------------------------------------------------------------------------- #
# L4 licensing scope (retrieval + FK neighborhood, decoupled from recall)
# --------------------------------------------------------------------------- #


def test_licenses_fk_neighbor_not_retrieved(corpus):
    # Decoupling L4 from retrieval recall: "total revenue" retrieves only the
    # transaction table, but its 1-hop FK neighbors (customers, rootbeer) are
    # licensed too, so an answer that legitimately needs one is not refused just
    # because the lexical retriever under-recalled.
    from governed_bi.graph import build_graph, plan_joins
    from governed_bi.analyst.context import assemble_context
    from governed_bi.analyst.governance import _licensed_table_ids

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
