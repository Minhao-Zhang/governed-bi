"""Tests for the SQL semantic-cache fast path (server.cache + flow wiring).

The embedder is the deterministic HashingEmbedder (no network). Flow integration
executes against the committed beer_factory DB (skipped if absent).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector, column_allowlist
from governed_bi.llm import HashingEmbedder
from governed_bi.server import SqlCache, TemplateSqlGenerator, answer_question
from governed_bi.server.answer import ReliabilityTier
from governed_bi.server.flow import _try_cache_hit

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"

REVENUE_Q = "What is the total revenue?"


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


def _put(cache, question, sql, licensed):
    cache.put(
        question,
        sql,
        licensed_tables=licensed,
        tables_used=frozenset({"tbl_beer_factory_transaction"}),
        metric_id="metric_revenue",
        join_ids=[],
        min_join_confidence=1.0,
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
    cache = SqlCache(HashingEmbedder())
    _put(cache, REVENUE_Q, 'SELECT SUM(PurchasePrice) FROM "transaction"', frozenset())  # empty scope
    allowlist = column_allowlist(corpus)

    result = _try_cache_hit(
        cache, REVENUE_Q, mem_gateway, identity, settings, allowlist, "sqlite", {}
    )
    assert result is None  # blocked at L4 re-check -> fall through


# --------------------------------------------------------------------------- #
# Flow integration
# --------------------------------------------------------------------------- #


class _CountingGenerator:
    def __init__(self):
        self.n = 0
        self._inner = TemplateSqlGenerator()

    def generate(self, *args, **kwargs):
        self.n += 1
        return self._inner.generate(*args, **kwargs)


def test_flow_miss_then_hit_skips_generation(bird_gateway, corpus, settings, identity):
    cache = SqlCache(HashingEmbedder())
    gen = _CountingGenerator()

    def ask():
        return answer_question(
            REVENUE_Q,
            identity,
            corpus=corpus,
            gateway=bird_gateway,
            settings=settings,
            session_id="s",
            sql_generator=gen,
            cache=cache,
        )

    first = ask()
    assert first.tier is ReliabilityTier.governed
    assert gen.n == 1  # miss ran the generator
    assert not first.provenance.get("cache_hit")
    assert len(cache) == 1  # governed answer written back

    second = ask()
    assert second.tier is ReliabilityTier.governed
    assert second.provenance["cache_hit"] is True
    assert gen.n == 1  # hit did NOT call the generator again
    assert second.sql == first.sql
