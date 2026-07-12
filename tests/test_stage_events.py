"""Tests for the ``on_event`` stage callback on the serve flow (Phase 1 of the
LangGraph rework; see docs/langgraph-rework-plan.md).

The callback is best-effort progress instrumentation: it fires a small
``{"stage": ...}`` dict at each pipeline stage, never changes the answer, and is
a no-op when omitted. Logic paths run on in-memory SQLite; the executed paths use
the committed beer_factory DB (skipped if absent).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm import HashingEmbedder
from governed_bi.server import SqlCache, answer_question
from governed_bi.server.answer import ReliabilityTier
from governed_bi.server.sqlgen import GeneratedSql

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"

REVENUE_Q = "What is the total revenue?"


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


class _Recorder:
    """Collects stage events; exposes the ordered stage names for assertions."""

    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(event)

    @property
    def stages(self) -> list[str]:
        return [e["stage"] for e in self.events]


def _answer(question, gateway, corpus, settings, identity, **kw):
    return answer_question(
        question, identity, corpus=corpus, gateway=gateway, settings=settings, session_id="s", **kw
    )


# --------------------------------------------------------------------------- #
# Fail-closed paths (no execution needed)
# --------------------------------------------------------------------------- #


def test_events_refuse_gate(mem_gateway, corpus, settings, identity):
    rec = _Recorder()
    ans = _answer(
        "How many employees work at the factory?", mem_gateway, corpus, settings, identity,
        on_event=rec,
    )
    assert ans.tier is ReliabilityTier.refused
    assert rec.stages == ["route", "refuse_gate"]
    assert rec.events[0]["route"]  # route value carried
    assert rec.events[1]["negative_example"]  # which curated example matched


def test_events_no_coverage_stops_after_declined_generate(mem_gateway, corpus, settings, identity):
    # The template generator declines (no metric), so the loop breaks after one
    # generate: no guardrail/execute/compose, and the answer is a refusal.
    rec = _Recorder()
    ans = _answer("Tell me about the weather on Mars", mem_gateway, corpus, settings, identity, on_event=rec)
    assert ans.tier is ReliabilityTier.refused
    assert rec.stages == ["route", "retrieve", "generate"]
    assert rec.events[-1]["attempt"] == 1


# --------------------------------------------------------------------------- #
# Governed + repair (executes against the committed DB)
# --------------------------------------------------------------------------- #


def test_events_governed_end_to_end(bird_gateway, corpus, settings, identity):
    rec = _Recorder()
    ans = _answer(REVENUE_Q, bird_gateway, corpus, settings, identity, on_event=rec)
    assert ans.tier is ReliabilityTier.governed
    assert rec.stages == ["route", "retrieve", "generate", "guardrail", "execute", "compose"]
    guardrail = next(e for e in rec.events if e["stage"] == "guardrail")
    assert guardrail["passed"] is True
    assert guardrail["failed_layer"] is None


def test_events_repair_loop_refires_generate_and_guardrail(bird_gateway, corpus, settings, identity):
    # First attempt is blocked at L4 (out-of-scope table, repairable); given the
    # feedback the generator repairs to a valid query that executes. The stage rail
    # shows generate/guardrail firing twice, then execute + compose.
    class RepairingGenerator:
        def generate(self, question, retrieval, corpus, *, feedback=(), context=None):
            if not feedback:
                return GeneratedSql(
                    sql="SELECT StarRating FROM rootbeerreview",
                    tables_used=frozenset({"tbl_beer_factory_rootbeerreview"}),
                )
            return GeneratedSql(
                sql='SELECT SUM(PurchasePrice) AS total_revenue FROM "transaction"',
                tables_used=frozenset({"tbl_beer_factory_transaction"}),
                metric_id="metric_revenue",
            )

    rec = _Recorder()
    ans = _answer(
        "total revenue", bird_gateway, corpus, settings, identity,
        sql_generator=RepairingGenerator(), on_event=rec,
    )
    assert ans.tier is ReliabilityTier.lineage  # repaired
    assert rec.stages == [
        "route", "retrieve", "generate", "guardrail", "generate", "guardrail", "execute", "compose",
    ]
    guardrails = [e for e in rec.events if e["stage"] == "guardrail"]
    assert guardrails[0]["passed"] is False
    assert guardrails[0]["failed_layer"] == "term_semantics"
    assert guardrails[0]["attempt"] == 1
    assert guardrails[1]["passed"] is True
    assert guardrails[1]["attempt"] == 2


def test_events_cache_hit_short_circuits(bird_gateway, corpus, settings, identity):
    # A warm cache serves the second turn without retrieval/generation; the stage
    # rail collapses to route -> cache_hit.
    cache = SqlCache(HashingEmbedder())

    def serve(rec):
        return answer_question(
            REVENUE_Q, identity, corpus=corpus, gateway=bird_gateway, settings=settings,
            session_id="s", cache=cache, on_event=rec,
        )

    first = _Recorder()
    serve(first)
    assert "compose" in first.stages  # miss ran the full pipeline

    second = _Recorder()
    ans = serve(second)
    assert ans.provenance["cache_hit"] is True
    assert second.stages == ["route", "cache_hit"]
    assert second.events[-1]["metric_id"] == "metric_revenue"


# --------------------------------------------------------------------------- #
# Best-effort contract
# --------------------------------------------------------------------------- #


def test_events_omitted_is_a_noop(bird_gateway, corpus, settings, identity):
    # The default (no callback) must behave exactly as before: a governed answer.
    ans = _answer(REVENUE_Q, bird_gateway, corpus, settings, identity)
    assert ans.tier is ReliabilityTier.governed


def test_events_callback_failure_never_breaks_the_answer(bird_gateway, corpus, settings, identity):
    # A callback that raises is swallowed: progress is best-effort and must never
    # turn a governed answer into an error.
    def boom(event):
        raise RuntimeError("listener down")

    ans = _answer(REVENUE_Q, bird_gateway, corpus, settings, identity, on_event=boom)
    assert ans.tier is ReliabilityTier.governed
    assert ans.result is not None
