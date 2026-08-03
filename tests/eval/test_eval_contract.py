"""Acceptance tests for Parcel G — authored against the plan, not the impl.

Effects asserted with hand-built fixtures. Do not re-derive gate logic here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from governed_bi.datasource.sqlite import SqliteConnector
from governed_bi.eval.arms import oracle_arm, stub_arm
from governed_bi.eval.grade import grade_turn, result_fingerprint
from governed_bi.eval.harness import run_arm, run_comparison
from governed_bi.eval.oracle import oracle_grade
from governed_bi.eval.report import (
    arm_population,
    comparison_quotable,
    context_hashes_distinct,
    headline_ex,
    paired_ex,
    summarise,
)
from governed_bi.measure.gates import Verdict
from governed_bi.measure.stats import mcnemar


def _fixture_db(tmp_path: Path) -> tuple[Path, SqliteConnector]:
    db = tmp_path / "customers.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO customers VALUES (1, 'a'), (2, 'b')")
    conn.commit()
    conn.close()
    connector = SqliteConnector(db)
    connector._connect()  # noqa: SLF001
    return db, connector


def _questions() -> list[dict]:
    return [
        {
            "question_id": "q1",
            "question": "how many customers",
            "db_id": "main",
            "gold_sql": "SELECT COUNT(*) AS n FROM customers",
        },
        {
            "question_id": "q2",
            "question": "list customer ids",
            "db_id": "main",
            "gold_sql": "SELECT id FROM customers ORDER BY id",
        },
    ]


def _clean_row(qid: str, **overrides) -> dict:
    row = {
        "question_id": qid,
        "correct": True,
        "crashed": False,
        "context_hash": f"hash-{qid}-a",
        "facet_channels": {"schema": "ran"},
        "facet_degraded": False,
        "guardrail_error": False,
        "re_served": False,
        "negative_failed_open": False,
        "outcome": "answered",
    }
    row.update(overrides)
    return row


def test_crash_stays_crashed_not_refused() -> None:
    grade = grade_turn(outcome="crashed")
    assert grade["correct"] is False
    assert grade["detail"] == "crashed"
    refused = grade_turn(outcome="refused")
    assert refused["detail"] == "refused"
    assert grade["detail"] != refused["detail"]


def test_oracle_grades_gold_sql_ex_one(tmp_path: Path) -> None:
    _, connector = _fixture_db(tmp_path)
    q = _questions()[0]
    row = oracle_grade(q, connector)
    assert row["outcome"] == "answered"
    assert row["correct"] is True
    assert row["crashed"] is False

    arm = oracle_arm(connector=connector)
    rows = run_arm(_questions(), arm)
    assert all(r["correct"] for r in rows)
    pop = arm_population(rows, label="oracle")
    ex = headline_ex(pop)
    assert ex.is_measured and ex.value == 1.0


def test_context_hash_distinctness_pass_and_fail() -> None:
    a = arm_population(
        [_clean_row(f"q{i}", context_hash=f"a-{i}") for i in range(20)],
        label="arm_a",
    )
    b = arm_population(
        [_clean_row(f"q{i}", context_hash=f"b-{i}") for i in range(20)],
        label="arm_b",
    )
    same = arm_population(
        [_clean_row(f"q{i}", context_hash=f"a-{i}") for i in range(20)],
        label="arm_same",
    )
    ok = context_hashes_distinct(a, b)
    assert ok.verdict is Verdict.passed
    bad = context_hashes_distinct(a, same)
    assert bad.verdict is Verdict.failed


def test_mcnemar_uses_same_population_as_headline() -> None:
    rows_a = [_clean_row(f"q{i}", correct=(i % 2 == 0)) for i in range(10)]
    rows_b = [_clean_row(f"q{i}", correct=True) for i in range(10)]
    a = arm_population(rows_a, label="a")
    b = arm_population(rows_b, label="b")
    shared = a.units & b.units
    a_s = a.restrict(lambda r: str(r["question_id"]) in shared, "shared questions")
    b_s = b.restrict(lambda r: str(r["question_id"]) in shared, "shared questions")
    head_a = headline_ex(a_s)
    head_b = headline_ex(b_s)
    result = paired_ex(a_s, b_s)
    again = mcnemar(a_s, b_s, "correct")
    assert again.n_pairs == result.n_pairs == a_s.n
    assert again.only_a == result.only_a and again.only_b == result.only_b
    assert head_a.is_measured and head_b.is_measured
    assert result.delta.is_measured
    assert result.delta.value == pytest.approx(head_b.value - head_a.value)


def test_quotable_false_when_crash_rate_positive() -> None:
    a = arm_population(
        [_clean_row(f"q{i}", context_hash=f"a{i}") for i in range(10)], label="clean"
    )
    b = arm_population(
        [
            _clean_row("q0", correct=False, crashed=True, outcome="crashed", context_hash="b0"),
            *[_clean_row(f"q{i}", context_hash=f"b{i}") for i in range(1, 10)],
        ],
        label="crashy",
    )
    ok, _results_a, results_b, _ctx = comparison_quotable(a, b)
    assert not ok
    assert any(r.field == "outcome" and r.verdict is Verdict.failed for r in results_b)


def test_eval_imports_one_mcnemar() -> None:
    import governed_bi.eval.report as report_mod
    import governed_bi.measure.stats as stats_mod

    assert report_mod.mcnemar is stats_mod.mcnemar


def test_stub_arm_invokes_serve(tmp_path: Path) -> None:
    _, connector = _fixture_db(tmp_path)
    rows = run_arm(_questions()[:1], stub_arm(connector=connector))
    assert len(rows) == 1
    assert rows[0]["outcome"] in {"answered", "refused", "crashed"}
    assert rows[0]["crashed"] == (rows[0]["outcome"] == "crashed")
    assert "question_id" in rows[0]


def test_result_fingerprint_order_insensitive() -> None:
    a = result_fingerprint(["id"], [[2], [1]], order_sensitive=False)
    b = result_fingerprint(["id"], [[1], [2]], order_sensitive=False)
    assert a == b
    c = result_fingerprint(["id"], [[2], [1]], order_sensitive=True)
    d = result_fingerprint(["id"], [[1], [2]], order_sensitive=True)
    assert c != d


def test_summarise_pair_runs(tmp_path: Path) -> None:
    _, connector = _fixture_db(tmp_path)
    questions = _questions()
    arms = run_comparison(
        questions,
        [oracle_arm(connector=connector), stub_arm(connector=connector)],
    )
    summary = summarise(arms, pair=("oracle", "stub"))
    assert "arms" in summary and "oracle" in summary["arms"]
    assert summary["comparison"]["pair"] == ("oracle", "stub")
