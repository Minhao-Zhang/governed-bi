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

from governed_bi.analyst import Route, bind_terms, route_intent
from governed_bi.analyst.answer import (
    ReliabilityTier,
    SemanticAssurance,
    UncertaintySignals,
    reliability_tier,
    semantic_assurance,
)
from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.retrieval import retrieve

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
    assert semantic_assurance(UncertaintySignals()) is SemanticAssurance.unflagged
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
    from governed_bi.analyst.context import assemble_context
    from governed_bi.analyst.governance import _licensed_table_ids
    from governed_bi.graph import build_graph, plan_joins

    graph = build_graph(corpus)
    retrieval = retrieve(corpus, "total revenue")
    assert set(retrieval.table_ids) == {"tbl_beer_factory_transaction"}  # retrieval missed the rest

    join_ids = plan_joins(graph, set(retrieval.table_ids)).join_ids
    licensed_ids = _licensed_table_ids(corpus, graph, retrieval, join_ids)
    # The guardrail's allowed_tables is the context's physical names, so check that.
    licensed = assemble_context(corpus, retrieval, licensed_table_ids=licensed_ids).allowed_table_names()

    # Names are schema-qualified (the engine is uniformly multi-schema).
    assert "beer_factory.transaction" in licensed  # the retrieved table
    assert "beer_factory.customers" in licensed  # 1-hop FK neighbor retrieval never surfaced
    assert "beer_factory.rootbeer" in licensed  # 1-hop FK neighbor retrieval never surfaced
    assert "beer_factory.rootbeerreview" not in licensed  # 3 hops out: still not licensed


# --------------------------------------------------------------------------- #
# AUDIT C2: the stamp must be able to say something about the EVIDENCE
# --------------------------------------------------------------------------- #


def test_weak_retrieval_lowers_the_stamp():
    from governed_bi.analyst.answer import (
        SemanticAssurance,
        UncertaintySignals,
        semantic_assurance,
    )

    clean = UncertaintySignals()
    assert semantic_assurance(clean) is SemanticAssurance.unflagged

    off_corpus = UncertaintySignals(weak_retrieval=True)
    assert semantic_assurance(off_corpus) is SemanticAssurance.heuristic
    assert "weak_retrieval" in off_corpus.fired()


def test_weak_retrieval_fires_only_on_zero_coverage():
    from governed_bi.analyst.governance import _weak_retrieval

    assert _weak_retrieval({"retrieval_lexical_coverage": 0.0}) is True
    assert _weak_retrieval({"retrieval_lexical_coverage": 0.5}) is False
    # No retrieval happened (e.g. a pre-existing cache entry): claim nothing.
    assert _weak_retrieval({}) is False
    assert _weak_retrieval({"retrieval_lexical_coverage": None}) is False


def test_off_corpus_question_reports_zero_coverage_while_still_returning_tables(corpus):
    """The C2 failure shape: a full table list for a question the corpus knows
    nothing about. Coverage is what distinguishes it from a real question."""
    grounded = retrieve(corpus, "total transaction amount by customer")
    off_corpus = retrieve(corpus, "what is the airspeed of a swallow")

    assert grounded.lexical_coverage is not None and grounded.lexical_coverage > 0.0
    assert off_corpus.lexical_coverage == 0.0
    # Retrieval still hands the model a full slate of tables — hence the flag.
    assert off_corpus.table_ids


def test_coverage_is_one_when_the_question_has_no_content_terms(corpus):
    """"How many are there?" is underspecified, not uncovered — do not conflate them."""
    assert retrieve(corpus, "how many are there").lexical_coverage == 1.0
