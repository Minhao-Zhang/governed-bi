"""Governance-invariant contract (ADR 0002 / build guide P0.3).

These assert governance outcomes that both the current deterministic flow and the
agent path must satisfy. Model-independent cases use a rogue SqlGenerator or the
refuse-gate; the agent path uses FakeToolModel trajectories.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn
from governed_bi.server import answer_question
from governed_bi.server.agent import answer_question_agent
from governed_bi.server.answer import ReliabilityTier, SemanticAssurance
from governed_bi.server.sqlgen import GeneratedSql

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"
TXN = "tbl_beer_factory_transaction"


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


def _flow_answer(question, gateway, corpus, settings, identity, **kw):
    return answer_question(
        question,
        identity,
        corpus=corpus,
        gateway=gateway,
        settings=settings,
        session_id="invariant",
        **kw,
    )


# --------------------------------------------------------------------------- #
# Invariant #1 — refuse-gate before any SQL
# --------------------------------------------------------------------------- #


def test_invariant_negative_example_refuses(mem_gateway, corpus, settings, identity):
    ans = _flow_answer(
        "How many employees work at the factory?",
        mem_gateway,
        corpus,
        settings,
        identity,
    )
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["refused_by"] == "refuse_gate"
    assert ans.sql is None
    assert ans.safety_clearance is False
    assert ans.semantic_assurance is SemanticAssurance.none


def test_invariant_negative_example_refuses_on_agent_path(
    mem_gateway, corpus, settings, identity
):
    # Refuse-gate runs before the agent; model is never invoked.
    ans = answer_question_agent(
        "How many employees work at the factory?",
        identity,
        corpus=corpus,
        gateway=mem_gateway,
        settings=settings,
        session_id="invariant-agent",
        model=FakeToolModel(responses=[AIMessage(content="should not run")]),
    )
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["refused_by"] == "refuse_gate"
    assert ans.sql is None
    assert ans.safety_clearance is False


# --------------------------------------------------------------------------- #
# Invariant #3 — L2 policy_blacklist is a hard stop (no repair coaching)
# --------------------------------------------------------------------------- #


def test_invariant_l2_sql_hard_refuses(mem_gateway, corpus, settings, identity):
    class Rogue:
        def generate(self, question, retrieval, corpus, *, feedback=(), context=None, **_kw):
            return GeneratedSql(sql="DROP TABLE customers", tables_used=frozenset())

    ans = _flow_answer(
        "total revenue",
        mem_gateway,
        corpus,
        settings,
        identity,
        sql_generator=Rogue(),
    )
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["failed_layer"] == "policy_blacklist"
    assert ans.provenance["attempts"] == 1
    assert ans.sql is None
    assert ans.safety_clearance is False


def test_invariant_l2_sql_hard_refuses_on_agent_path(
    bird_gateway, corpus, settings, identity
):
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn("run_query", {"sql": "DROP TABLE customers"}, "c2"),
    ]
    ans = answer_question_agent(
        "total revenue",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="invariant-l2",
        model=FakeToolModel(responses=turns),
    )
    assert ans.tier is ReliabilityTier.refused
    assert ans.provenance["failed_layer"] == "policy_blacklist"
    assert ans.sql is None
    assert ans.safety_clearance is False


# --------------------------------------------------------------------------- #
# Safety-clearance stamping matches today's flow
# --------------------------------------------------------------------------- #


def test_invariant_safety_clearance_on_governed_answer(
    bird_gateway, corpus, settings, identity
):
    ans = _flow_answer(
        "What is the total revenue?",
        bird_gateway,
        corpus,
        settings,
        identity,
    )
    assert ans.tier is ReliabilityTier.governed
    assert ans.safety_clearance is True
    assert ans.semantic_assurance is SemanticAssurance.certified
    assert ans.sql is not None


def test_invariant_safety_clearance_on_agent_path(
    bird_gateway, corpus, settings, identity
):
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
            "c2",
        ),
        AIMessage(content="done"),
    ]
    ans = answer_question_agent(
        "What is the total revenue?",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="invariant-ok",
        model=FakeToolModel(responses=turns),
    )
    assert ans.tier is ReliabilityTier.governed
    assert ans.safety_clearance is True
    assert ans.semantic_assurance is SemanticAssurance.certified
    assert ans.sql is not None


def test_invariant_safety_clearance_false_on_refusal(
    mem_gateway, corpus, settings, identity
):
    ans = _flow_answer(
        "Tell me about the weather on Mars",
        mem_gateway,
        corpus,
        settings,
        identity,
    )
    assert ans.tier is ReliabilityTier.refused
    assert ans.safety_clearance is False
    assert ans.semantic_assurance is SemanticAssurance.none
