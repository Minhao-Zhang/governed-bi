"""Tests for the SQL semantic-cache fast path (analyst.cache + analyst.governance wiring).

The embedder is the deterministic HashingEmbedder (no network). The cache-hit
integration test executes against the committed beer_factory DB (skipped if absent).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.analyst import SqlCache
from governed_bi.analyst.governance import _try_cache_hit
from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector, column_allowlist
from governed_bi.llm import HashingEmbedder

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"

REVENUE_Q = "What is the total revenue?"


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


@pytest.fixture
def bird_gateway():
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    yield Gateway(conn)
    conn.close()


def _put(cache, question, sql, licensed):
    cache.put(
        question,
        sql,
        licensed_tables=licensed,
        tables_used=frozenset({"tbl_beer_factory_transaction"}),
        metric_id="metric_revenue",
    )


# --------------------------------------------------------------------------- #
# Unit: SqlCache
# --------------------------------------------------------------------------- #


def test_exact_question_is_a_hit():
    cache = SqlCache(HashingEmbedder())
    _put(cache, REVENUE_Q, "SELECT 1", frozenset({"transaction"}))
    hit = cache.lookup(REVENUE_Q)
    assert hit is not None
    assert hit.sql == "SELECT 1"


def test_unrelated_question_is_a_miss():
    cache = SqlCache(HashingEmbedder())
    _put(cache, REVENUE_Q, "SELECT 1", frozenset({"transaction"}))
    assert cache.lookup("what colour is the customer's phone") is None


def test_ttl_expiry_uses_the_clock():
    clock = {"t": 0.0}
    cache = SqlCache(HashingEmbedder(), ttl_seconds=900, clock=lambda: clock["t"])
    _put(cache, REVENUE_Q, "SELECT 1", frozenset({"transaction"}))

    clock["t"] = 800.0  # still fresh
    assert cache.lookup(REVENUE_Q) is not None

    clock["t"] = 1000.0  # past TTL
    assert cache.lookup(REVENUE_Q) is None
    assert len(cache) == 0  # expired entry was purged


def test_gate_is_respected():
    strict = SqlCache(HashingEmbedder(), gate=0.999)
    _put(strict, REVENUE_Q, "SELECT 1", frozenset({"transaction"}))
    # Same bag of words -> cosine 1.0 -> still a hit even at a strict gate.
    assert strict.lookup("revenue total the what is") is not None


# --------------------------------------------------------------------------- #
# _try_cache_hit fail-closed behavior
# --------------------------------------------------------------------------- #


def test_stale_hit_that_no_longer_passes_guardrails_falls_through(mem_gateway, corpus, settings, identity):
    # An entry whose licensed_tables no longer admits its table (a stand-in for a
    # corpus change) must re-fail L4 on lookup and return None (fall through),
    # never be served.
    from governed_bi.graph import build_graph

    cache = SqlCache(HashingEmbedder())
    _put(cache, REVENUE_Q, 'SELECT SUM(PurchasePrice) FROM "transaction"', frozenset())  # empty scope
    allowlist = column_allowlist(corpus)
    graph = build_graph(corpus)

    result = _try_cache_hit(
        cache, REVENUE_Q, mem_gateway, identity, settings, allowlist, "sqlite", graph, {}
    )
    assert result is None  # blocked at L4 re-check -> fall through


# The end-to-end miss→hit path (serve produces a governed answer, then the cache
# short-circuits the next turn) now runs through the agentic serve core, which
# needs a live model — covered by scripts/live_smoke.py, not the hermetic suite.


# --------------------------------------------------------------------------- #
# AUDIT R7: the key was the question embedding and nothing else.
# --------------------------------------------------------------------------- #


def test_a_different_threshold_is_a_miss_not_a_near_match():
    """The reported failure: ~0.94 cosine cleared the 0.92 gate, so the cache
    re-executed the wrong predicate and served it with full freshness."""
    from governed_bi.analyst.cache import SqlCache

    cache = SqlCache(HashingEmbedder())
    cache.put(
        "customers who spent more than 100 dollars",
        "SELECT id FROM customers WHERE spend > 100",
        licensed_tables=frozenset({"demo.customers"}),
        tables_used=frozenset({"tbl_demo_customers"}),
        metric_id=None,
    )

    assert cache.lookup("customers who spent more than 100 dollars") is not None
    assert cache.lookup("customers who spent more than 200 dollars") is None


def test_a_different_string_literal_is_also_a_miss():
    from governed_bi.analyst.cache import SqlCache

    cache = SqlCache(HashingEmbedder())
    cache.put(
        "orders in the 'north' region",
        "SELECT * FROM orders WHERE region = 'north'",
        licensed_tables=frozenset(),
        tables_used=frozenset(),
        metric_id=None,
    )
    assert cache.lookup("orders in the 'south' region") is None


def test_entries_from_another_scope_are_invisible():
    """One process serving two corpora / dialects / schemas must not cross over."""
    from governed_bi.analyst.cache import CacheScope, SqlCache

    a = SqlCache(HashingEmbedder(), scope=CacheScope(corpus_hash="aaa", dialect="sqlite"))
    a.put(
        "how many customers",
        "SELECT COUNT(*) FROM customers",
        licensed_tables=frozenset(),
        tables_used=frozenset(),
        metric_id=None,
    )
    assert a.lookup("how many customers") is not None

    b = SqlCache(HashingEmbedder(), scope=CacheScope(corpus_hash="bbb", dialect="sqlite"))
    b._entries = a._entries  # same store, different deployment
    assert b.lookup("how many customers") is None


def test_a_literal_free_question_still_caches():
    from governed_bi.analyst.cache import SqlCache

    cache = SqlCache(HashingEmbedder())
    cache.put(
        "how many customers are there",
        "SELECT COUNT(*) FROM customers",
        licensed_tables=frozenset(),
        tables_used=frozenset(),
        metric_id=None,
    )
    assert cache.lookup("how many customers are there") is not None
