"""An absent grade is not a wrong answer, and the dataset's own warnings get read.

Both halves are the same defect seen from two sides, and both are regressions this repository
has already paid for once:

* ``grade_turn`` returned ``correct=False`` when there was no gold to compare against, and
  ``harness`` then coerced it with ``bool()``. ``Population.count`` refuses to measure a
  population containing an absent outcome, and carries an *import-time* guard saying so — but
  the coercion happened upstream of the guard, so a gold that would not execute was recorded as
  a question the system got wrong.
* ``gold_result_hashes_<dsn>.jsonl`` and ``leakage_test_qids.json`` ship with the dataset and had
  no reader. The first is why the grader-ceiling arm measured nothing; the second is 9 of the
  pooled arm's 1,351 questions being scored despite the dataset flagging them.

The tests that matter most here are the **guards** on the digest wiring. Attaching a published
digest to an order-sensitive question would turn a fix into a 23-question regression, because
``hash_lenient`` always sorts and those questions are graded with row order preserved.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from governed_bi.eval.datalake import (
    attach_gold_fingerprints,
    attach_quality_flags,
    dataset_leakage_qids,
    retrieval_funnel,
)
from governed_bi.eval.grade import grade_turn
from governed_bi.measure.population import Population


def test_no_gold_is_unmeasured_not_incorrect() -> None:
    grade = grade_turn(outcome="answered", pred_columns=["a"], pred_rows=[[1]])
    assert grade["correct"] is None, "no gold to compare against is not a wrong answer"
    assert grade["detail"] == "missing_gold"


def test_a_missing_prediction_is_still_the_models_fault() -> None:
    """The other side of the line: unexecutable *predicted* SQL is a real failure."""
    grade = grade_turn(outcome="answered", gold_columns=["a"], gold_rows=[[1]])
    assert grade["correct"] is False
    assert grade["detail"] == "missing_prediction"


def test_crashes_and_refusals_stay_incorrect() -> None:
    """Deliberately ``False``: the turn happened and produced no answer."""
    assert grade_turn(outcome="crashed")["correct"] is False
    assert grade_turn(outcome="refused")["correct"] is False


def test_population_refuses_a_rate_over_an_unmeasured_row() -> None:
    """Why the ``None`` has to survive: this is the guard it exists to reach."""
    rows = [
        {"question_id": "a", "correct": True},
        {"question_id": "b", "correct": grade_turn(outcome="answered", pred_columns=["a"],
                                                   pred_rows=[[1]])["correct"]},
    ]
    measured = Population.of("arm", rows).rate("correct")
    assert not measured.is_measured
    assert "not a negative one" in measured.why


def _funnel_rows(correct: object) -> list[dict]:
    return [
        {
            "question_id": "q1",
            "db_id": "s",
            "licensed_schemas": ["s"],
            "licensed": ["s.t"],
            "outcome": "answered",
            "correct": correct,
        }
    ]


def test_the_funnel_does_not_charge_the_pipeline_for_an_unmeasured_row() -> None:
    gold = {"q1": 'SELECT "x" FROM "s"."t"'}
    wrong = retrieval_funnel(_funnel_rows(False), gold)
    unmeasured = retrieval_funnel(_funnel_rows(None), gold)

    assert wrong["conditional"]["correct"]["rate"] == 0.0, "a wrong answer is a zero"
    assert wrong["counts"]["graded"] == 1

    assert unmeasured["counts"]["graded"] == 0
    assert unmeasured["counts"]["unmeasured"] == 1
    assert unmeasured["conditional"]["correct"]["rate"] is None, (
        "with nothing graded there is no EX; 0.000 would be a claim"
    )
    assert unmeasured["conditional"]["graded"]["rate"] == 0.0, (
        "the grader's own coverage is what should read zero here"
    )
    assert unmeasured["end_to_end"]["rate"] is None


def _dataset(tmp_path: Path, *, gold_sql: str, **overrides: object) -> Path:
    """A dataset directory holding one digest row for question ``q1``."""
    row = {
        "question_id": "q1",
        "db_id": "s",
        "dsn_key": "rename_decoy",
        "sql_sha256": hashlib.sha256(gold_sql.encode("utf-8")).hexdigest(),
        "nrows": 1,
        "hash_lenient": "lenient-digest",
        "hash_strict": "strict-digest",
        "error": None,
    }
    row.update(overrides)
    (tmp_path / "gold_result_hashes_rename_decoy.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )
    return tmp_path


def test_the_published_digest_is_attached() -> None:
    """The plain case: this is what makes the grader-ceiling arm measurable."""
    gold = 'SELECT "x" FROM "s"."t"'
    questions = [{"question_id": "q1", "gold_sql": gold}]
    counts = attach_gold_fingerprints(
        questions, _dataset(Path(_tmp()), gold_sql=gold), dsn_key="rename_decoy"
    )
    assert counts["attached"] == 1
    assert questions[0]["gold_fingerprint"] == "lenient-digest"


def test_an_order_sensitive_question_is_never_given_the_sorted_digest() -> None:
    """``hash_lenient`` is ``normalise_result``, which sorts. These questions are graded unsorted.

    Attaching it anyway would compare an order-preserving prediction digest against an
    order-insensitive gold digest and fail every one of them — 23 questions in the pooled arm.
    """
    gold = 'SELECT "x" FROM "s"."t"'
    questions = [{"question_id": "q1", "gold_sql": gold}]
    counts = attach_gold_fingerprints(
        questions,
        _dataset(Path(_tmp()), gold_sql=gold),
        dsn_key="rename_decoy",
        order_sensitive={"q1"},
    )
    assert counts["attached"] == 0
    assert counts["order_sensitive"] == 1
    assert "gold_fingerprint" not in questions[0], "falls back to executing the gold live"


def test_a_digest_recorded_for_a_different_statement_is_refused() -> None:
    """The dataset's statement can move under its own digest; it does on 2 of 1,351 today."""
    questions = [{"question_id": "q1", "gold_sql": 'SELECT "y" FROM "s"."t"'}]
    counts = attach_gold_fingerprints(
        questions,
        _dataset(Path(_tmp()), gold_sql='SELECT "x" FROM "s"."t"'),
        dsn_key="rename_decoy",
    )
    assert counts["statement_changed"] == 1
    assert "gold_fingerprint" not in questions[0]


def test_a_digest_from_another_database_or_a_failed_recording_is_refused() -> None:
    gold = 'SELECT "x" FROM "s"."t"'
    other = [{"question_id": "q1", "gold_sql": gold}]
    assert attach_gold_fingerprints(
        other, _dataset(Path(_tmp()), gold_sql=gold, dsn_key="base"), dsn_key="rename_decoy"
    )["other_database"] == 1

    failed = [{"question_id": "q1", "gold_sql": gold}]
    assert attach_gold_fingerprints(
        failed, _dataset(Path(_tmp()), gold_sql=gold, error="timeout"), dsn_key="rename_decoy"
    )["recorded_error"] == 1


def test_a_dataset_with_no_digest_file_attaches_nothing_and_says_so() -> None:
    questions = [{"question_id": "q1", "gold_sql": "SELECT 1"}]
    counts = attach_gold_fingerprints(questions, Path(_tmp()), dsn_key="rename_decoy")
    assert counts["no_file"] == 1
    assert counts["attached"] == 0


def test_leakage_ids_come_from_the_union_and_an_unreadable_file_raises() -> None:
    root = Path(_tmp())
    (root / "leakage_test_qids.json").write_text(
        json.dumps({"note": "n", "exact_gold_sql": ["a"], "union": ["a", "b"]}), encoding="utf-8"
    )
    assert dataset_leakage_qids(root) == {"a", "b"}

    assert dataset_leakage_qids(Path(_tmp())) == set(), "no file is a real 'none declared'"

    broken = Path(_tmp())
    (broken / "leakage_test_qids.json").write_text(json.dumps({"note": "n"}), encoding="utf-8")
    with pytest.raises(KeyError):
        dataset_leakage_qids(broken)


def test_quality_flags_tag_the_question_rather_than_dropping_it() -> None:
    questions = [{"question_id": "a"}, {"question_id": "b"}, {"question_id": "c"}]
    counts = attach_quality_flags(
        questions, leakage={"a"}, order_sensitive={"b", "a"}, exec_failed={"c"}
    )
    assert [q["quality_flags"] for q in questions] == [
        ["leakage", "order_sensitive"],
        ["order_sensitive"],
        ["exec_failed"],
    ]
    assert counts == {"leakage": 1, "order_sensitive": 2, "exec_failed": 1, "degenerate": 0}
    assert len(questions) == 3, "flagged, never filtered — the exclusion is the reader's call"


def test_a_gold_that_reads_no_table_is_flagged_degenerate() -> None:
    """The one flag derived here rather than published by the dataset.

    127 of the 1 351 test questions have a gold that is a frozen answer literal rather than a
    query, and the engine won 42 of them by accident (2026-08-09 full run, corpus 30872d3).
    They were found by hand-reading an artifact; the flag makes the next reader's headline
    recomputable without that.

    The three cases that matter are the boundaries, not the happy path: a real query is not
    degenerate, an *unparseable* gold is not degenerate either (that is the instrument failing,
    and collapsing it with "no tables" would shrink a denominator over a parser gap), and the
    flag composes with a dataset-published one rather than replacing it.
    """
    questions = [
        {"question_id": "real", "gold_sql": 'SELECT "a" FROM "s"."t"'},
        {"question_id": "frozen", "gold_sql": "SELECT \"v\".\"c0\" FROM (VALUES (42)) AS \"v\"(\"c0\")"},
        {"question_id": "unparseable", "gold_sql": "SELEKT nonsense FROM ("},
        {"question_id": "absent"},
        {"question_id": "both", "gold_sql": "SELECT 1", "x": None},
    ]
    counts = attach_quality_flags(questions, leakage={"both"})

    flags = {q["question_id"]: q["quality_flags"] for q in questions}
    assert flags["real"] == []
    assert flags["frozen"] == ["degenerate"]
    assert flags["unparseable"] == [], "an unparseable gold is the instrument failing, not a frozen literal"
    assert flags["absent"] == []
    assert flags["both"] == ["leakage", "degenerate"], "derived flags compose with published ones"
    assert counts["degenerate"] == 2, counts


def _tmp() -> str:
    """A fresh directory. ``tmp_path`` is per-test, and several tests here need several."""
    import tempfile

    return tempfile.mkdtemp()
