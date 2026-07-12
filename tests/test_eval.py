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
    eval_refuse_gate,
    execution_match,
    flow_refuser,
    flow_solver,
    run_arm,
)
from governed_bi.gateway import Gateway, Identity, SqliteConnector, column_allowlist

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"

pytestmark = pytest.mark.skipif(not BIRD_DB.exists(), reason="vendored beer_factory.sqlite not present")


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_server()


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

    result = run_arm(Arm.gold, gateway, BEER_FACTORY_EVAL, GoldSolver())
    assert result.ex == 1.0
    assert result.governed_path_adherence == 1.0


def test_curator_arm_via_flow(corpus, gateway, settings, identity):
    # The deterministic template solves the two metric questions and declines the
    # two count questions; the guardrails keep the decoy-touch rate at zero.
    solver = flow_solver(corpus, gateway, settings, identity)
    suspect = column_allowlist(corpus).suspect
    result = run_arm(Arm.curator, gateway, BEER_FACTORY_EVAL, solver, suspect_columns=suspect)
    assert result.n == 4
    assert result.ex == 0.5
    assert result.governed_path_adherence == 0.5
    assert result.decoy_touch_rate == 0.0


# --------------------------------------------------------------------------- #
# Refuse-gate
# --------------------------------------------------------------------------- #


def test_refuse_gate_scores(corpus, gateway, settings, identity):
    answerable = [it.question for it in BEER_FACTORY_EVAL if it.answerable_by_template]
    refused = flow_refuser(corpus, gateway, settings, identity)
    result = eval_refuse_gate(answerable, BEER_FACTORY_UNANSWERABLE, refused)
    assert result.refusal_accuracy == 1.0  # refuses all unanswerable questions
    assert result.false_refusal_rate == 0.0  # answers all it can
