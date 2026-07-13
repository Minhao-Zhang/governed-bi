"""Tests for the LangGraph serve harness (server.graph).

The core assertion is EQUIVALENCE: answer_question_graph must return the same
Answer as the plain answer_question for every path (governed, refuse-gate,
guardrail block, self-repair, decline, cache). Skipped if the ``agents`` extra
(langgraph) is absent. Governed/repair paths execute against the committed DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from governed_bi.config import Environment, Settings  # noqa: E402
from governed_bi.corpus import load_corpus  # noqa: E402
from governed_bi.gateway import Gateway, Identity, SqliteConnector  # noqa: E402
from governed_bi.llm import HashingEmbedder, StaticChatClient  # noqa: E402
from governed_bi.server import LlmSqlGenerator, SqlCache, answer_question  # noqa: E402
from governed_bi.server.answer import ReliabilityTier  # noqa: E402
from governed_bi.server.graph import answer_question_graph, build_serve_graph  # noqa: E402
from governed_bi.server.sqlgen import GeneratedSql  # noqa: E402

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"

REVENUE_SQL = 'SELECT SUM(PurchasePrice) AS total_revenue FROM "transaction"'


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_server()


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


def _assert_same(a, b, *, provenance_keys=()):
    assert a.tier is b.tier, f"{a.tier} != {b.tier}"
    assert a.sql == b.sql
    assert (a.escalation is None) == (b.escalation is None)
    for key in provenance_keys:
        assert a.provenance.get(key) == b.provenance.get(key), key


def _kw(gateway, corpus, settings, identity, **extra):
    return dict(
        corpus=corpus, gateway=gateway, settings=settings, identity=identity, session_id="s", **extra
    )


# --------------------------------------------------------------------------- #
# Graph structure
# --------------------------------------------------------------------------- #


def test_build_serve_graph_has_expected_nodes(corpus, mem_gateway, settings):
    graph = build_serve_graph(corpus, mem_gateway, settings)
    nodes = set(graph.get_graph().nodes)
    for name in ("ingest", "refuse_gate", "prepare", "cache", "retrieve", "generate", "guardrail", "execute", "refuse"):
        assert name in nodes


# --------------------------------------------------------------------------- #
# Equivalence with answer_question across every path
# --------------------------------------------------------------------------- #


def test_governed_matches_plain(bird_gateway, corpus, settings, identity):
    q = "What is the total revenue?"
    kw = _kw(bird_gateway, corpus, settings, identity)
    plain = answer_question(q, **kw)
    viagraph = answer_question_graph(q, **kw)
    assert plain.tier is ReliabilityTier.governed
    _assert_same(plain, viagraph, provenance_keys=("metric_id", "tables_used", "attempts", "min_join_confidence"))
    # The executed rows are carried identically on both harnesses.
    assert plain.result.rows == viagraph.result.rows


def test_narrator_and_result_match_plain(bird_gateway, corpus, settings, identity):
    # The narrator seam threads through the graph the same way it does the plain
    # flow: a single-response client yields identical text on both harnesses.
    from governed_bi.server import LlmAnswerNarrator

    q = "What is the total revenue?"
    plain = answer_question(
        q, narrator=LlmAnswerNarrator(StaticChatClient("Revenue: $18,496.")),
        **_kw(bird_gateway, corpus, settings, identity),
    )
    viagraph = answer_question_graph(
        q, narrator=LlmAnswerNarrator(StaticChatClient("Revenue: $18,496.")),
        **_kw(bird_gateway, corpus, settings, identity),
    )
    _assert_same(plain, viagraph, provenance_keys=("metric_id", "tables_used"))
    assert plain.text == viagraph.text == "Revenue: $18,496."
    assert plain.result.rows == viagraph.result.rows


def test_refuse_gate_matches_plain(mem_gateway, corpus, settings, identity):
    q = "How many employees work at the factory?"
    kw = _kw(mem_gateway, corpus, settings, identity)
    plain = answer_question(q, **kw)
    viagraph = answer_question_graph(q, **kw)
    assert plain.tier is ReliabilityTier.refused
    assert plain.provenance["refused_by"] == "refuse_gate"
    _assert_same(plain, viagraph, provenance_keys=("refused_by", "negative_example"))


def test_no_coverage_matches_plain(mem_gateway, corpus, settings, identity):
    q = "Tell me about the weather on Mars"
    kw = _kw(mem_gateway, corpus, settings, identity)
    plain = answer_question(q, **kw)
    viagraph = answer_question_graph(q, **kw)
    assert plain.tier is ReliabilityTier.refused
    _assert_same(plain, viagraph, provenance_keys=("refused_by",))


def test_guardrail_block_matches_plain(mem_gateway, corpus, settings, identity):
    class Rogue:
        def generate(self, question, retrieval, corpus, *, feedback=(), context=None):
            return GeneratedSql(
                sql="SELECT StarRating FROM rootbeerreview",
                tables_used=frozenset({"tbl_beer_factory_rootbeerreview"}),
            )

    kw = _kw(mem_gateway, corpus, settings, identity, sql_generator=Rogue())
    plain = answer_question("total revenue", **kw)
    viagraph = answer_question_graph("total revenue", **kw)
    assert plain.tier is ReliabilityTier.refused
    assert plain.provenance["failed_layer"] == "term_semantics"
    _assert_same(plain, viagraph, provenance_keys=("refused_by", "failed_layer", "attempts"))


def test_repair_matches_plain(bird_gateway, corpus, settings, identity):
    # Same fake-model script drives both entry points; both should repair to lineage.
    def make_gen():
        return LlmSqlGenerator(
            StaticChatClient(["SELECT StarRating FROM rootbeerreview", REVENUE_SQL]), dialect="sqlite"
        )

    plain = answer_question("total revenue", **_kw(bird_gateway, corpus, settings, identity, sql_generator=make_gen()))
    viagraph = answer_question_graph("total revenue", **_kw(bird_gateway, corpus, settings, identity, sql_generator=make_gen()))
    assert plain.tier is ReliabilityTier.lineage
    assert plain.provenance["attempts"] == 2
    _assert_same(plain, viagraph, provenance_keys=("attempts", "metric_id"))
    assert "repaired" in viagraph.provenance["uncertainty_flags"]


def test_repair_exhaustion_matches_plain(mem_gateway, corpus, settings, identity):
    # A generator that emits a distinct but always-off-scope query each attempt:
    # the graph's repair cycle must hit the cap and refuse, same as the plain loop.
    from governed_bi.server.flow import MAX_REPAIR_ATTEMPTS

    class AlwaysBad:
        def __init__(self):
            self.n = 0

        def generate(self, question, retrieval, corpus, *, feedback=(), context=None):
            self.n += 1
            return GeneratedSql(
                sql=f"SELECT StarRating AS c{self.n} FROM rootbeerreview",
                tables_used=frozenset({"tbl_beer_factory_rootbeerreview"}),
            )

    plain = answer_question("total revenue", **_kw(mem_gateway, corpus, settings, identity, sql_generator=AlwaysBad()))
    viagraph = answer_question_graph("total revenue", **_kw(mem_gateway, corpus, settings, identity, sql_generator=AlwaysBad()))
    assert plain.tier is ReliabilityTier.refused
    assert plain.provenance["attempts"] == MAX_REPAIR_ATTEMPTS
    _assert_same(plain, viagraph, provenance_keys=("refused_by", "failed_layer", "attempts"))


def test_grade_semantic_failures_matches_plain(mem_gateway, corpus, settings, identity):
    from dataclasses import replace

    from governed_bi.server.flow import MAX_REPAIR_ATTEMPTS

    settings = replace(settings, grade_semantic_failures=True)

    class AlwaysBad:
        def __init__(self):
            self.n = 0

        def generate(self, question, retrieval, corpus, *, feedback=(), context=None):
            self.n += 1
            return GeneratedSql(
                sql=f"SELECT StarRating AS c{self.n} FROM rootbeerreview",
                tables_used=frozenset({"tbl_beer_factory_rootbeerreview"}),
            )

    plain = answer_question(
        "total revenue", **_kw(mem_gateway, corpus, settings, identity, sql_generator=AlwaysBad())
    )
    viagraph = answer_question_graph(
        "total revenue", **_kw(mem_gateway, corpus, settings, identity, sql_generator=AlwaysBad())
    )
    assert plain.tier is ReliabilityTier.fenced_raw
    assert plain.provenance["graded_delivery"] is True
    assert plain.provenance["attempts"] == MAX_REPAIR_ATTEMPTS
    assert plain.sql is not None
    _assert_same(
        plain,
        viagraph,
        provenance_keys=("refused_by", "failed_layer", "attempts", "graded_delivery"),
    )


def test_llm_decline_matches_plain(mem_gateway, corpus, settings, identity):
    def make_gen():
        return LlmSqlGenerator(StaticChatClient("CANNOT_ANSWER"), dialect="sqlite")

    plain = answer_question("total revenue", **_kw(mem_gateway, corpus, settings, identity, sql_generator=make_gen()))
    viagraph = answer_question_graph("total revenue", **_kw(mem_gateway, corpus, settings, identity, sql_generator=make_gen()))
    assert plain.tier is ReliabilityTier.refused
    _assert_same(plain, viagraph, provenance_keys=("refused_by",))


# --------------------------------------------------------------------------- #
# Cache fast path through the graph
# --------------------------------------------------------------------------- #


def test_graph_cache_miss_then_hit(bird_gateway, corpus, settings, identity):
    cache = SqlCache(HashingEmbedder())
    graph = build_serve_graph(corpus, bird_gateway, settings, cache=cache)
    q = "What is the total revenue?"

    first = graph.invoke({"question": q, "identity": identity, "session_id": "s"})["answer"]
    assert first.tier is ReliabilityTier.governed
    assert not first.provenance.get("cache_hit")
    assert len(cache) == 1

    second = graph.invoke({"question": q, "identity": identity, "session_id": "s"})["answer"]
    assert second.provenance["cache_hit"] is True
    assert second.sql == first.sql
