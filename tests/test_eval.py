"""Tests for the eval scaffold: EX scorer, arm harness, refuse-gate.

These execute gold + candidate SQL against the committed beer_factory database,
so the whole module is skipped when it is not present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.eval import (
    BEER_FACTORY_EVAL,
    BEER_FACTORY_UNANSWERABLE,
    Arm,
    agent_refuser,
    agent_solver,
    eval_refuse_gate,
    execution_match,
    run_arm,
)
from governed_bi.gateway import Gateway, Identity, SqliteConnector, column_allowlist

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"

pytestmark = pytest.mark.skipif(not BIRD_DB.exists(), reason="vendored beer_factory.sqlite not present")

# The curator/refuse-gate arms now drive the agentic serve core, which needs a
# live model; the hermetic suite has none, so those two cases are live-only. The
# EX scorer and the gold self-solver stay fully offline (solver-agnostic).
requires_live_serve = pytest.mark.skip(
    reason="agent-only serve needs a live model; covered by scripts/live_smoke.py"
)


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


@pytest.fixture
def gateway():
    conn = SqliteConnector(BIRD_DB)
    yield Gateway(conn)
    conn.close()


@pytest.fixture
def settings():
    return Settings.for_env(Environment.dev)


@pytest.fixture
def identity():
    return Identity(user="eval", all_access=True)


# --------------------------------------------------------------------------- #
# EX scorer
# --------------------------------------------------------------------------- #


def test_ex_matches_equivalent_sql(gateway):
    # An alias does not change the result set.
    assert execution_match(
        'SELECT SUM(PurchasePrice) AS x FROM "transaction"',
        'SELECT SUM(PurchasePrice) FROM "transaction"',
        gateway,
    )


def test_ex_rejects_different_result(gateway):
    assert not execution_match(
        "SELECT COUNT(*) FROM customers",
        'SELECT SUM(PurchasePrice) FROM "transaction"',
        gateway,
    )


def test_ex_bad_sql_is_not_a_match(gateway):
    assert not execution_match("SELECT nope FROM missing", "SELECT 1", gateway)


def test_ex_empty_prediction_is_not_a_match(gateway):
    assert not execution_match("", "SELECT 1", gateway)


# --------------------------------------------------------------------------- #
# Arm harness
# --------------------------------------------------------------------------- #


def test_gold_self_solver_scores_perfect_ex(gateway):
    """A solver that returns the gold SQL must score EX 1.0 (harness sanity)."""

    class GoldSolver:
        def solve(self, question):
            return next((it.sql for it in BEER_FACTORY_EVAL if it.question == question), None)

    result = run_arm(Arm.baseline, gateway, BEER_FACTORY_EVAL, GoldSolver())
    assert result.ex == 1.0
    assert result.governed_path_adherence == 1.0


@requires_live_serve
def test_curator_arm_via_agent(corpus, gateway, settings, identity):
    # The agentic serve core answers the metric questions and the guardrails keep
    # the decoy-touch rate at zero. Live-only: the agent needs a real model.
    solver = agent_solver(corpus, gateway, settings, identity, model=None)
    suspect = column_allowlist(corpus).suspect
    result = run_arm(Arm.curated, gateway, BEER_FACTORY_EVAL, solver, suspect_columns=suspect)
    assert result.decoy_touch_rate == 0.0


# --------------------------------------------------------------------------- #
# Refuse-gate
# --------------------------------------------------------------------------- #


def test_eval_refuse_gate_scoring_math():
    """Offline: the scorer's two rates are computed independently — refusal
    accuracy on the unanswerable set, false-refusal on the answerable set — with
    no model. A predicate that refuses only questions containing 'weather'."""
    answerable = ["revenue?", "customers?", "weather?"]  # 1 of 3 wrongly refused
    unanswerable = ["staff weather", "payroll weather", "salary"]  # 2 of 3 refused
    result = eval_refuse_gate(answerable, unanswerable, lambda q: "weather" in q)
    assert result.false_refusal_rate == pytest.approx(1 / 3)
    assert result.refusal_accuracy == pytest.approx(2 / 3)
    # Empty answerable set -> 0.0 false-refusal (no division by zero).
    assert eval_refuse_gate([], unanswerable, lambda q: True).false_refusal_rate == 0.0


@requires_live_serve
def test_refuse_gate_scores(corpus, gateway, settings, identity):
    answerable = [it.question for it in BEER_FACTORY_EVAL if it.answerable_by_template]
    refused = agent_refuser(corpus, gateway, settings, identity, model=None)
    result = eval_refuse_gate(answerable, BEER_FACTORY_UNANSWERABLE, refused)
    assert result.refusal_accuracy == 1.0  # refuses all unanswerable questions
    assert result.false_refusal_rate == 0.0  # answers all it can
