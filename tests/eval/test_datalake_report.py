"""The report prints accuracy, coverage, the two halves, and disagreement."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from datalake_report import disagreement, execution_accuracy, load_halves, print_report  # noqa: E402


def test_unmeasured_rows_are_not_wrong_answers() -> None:
    ok, graded = execution_accuracy(
        [{"correct": True}, {"correct": False}, {"correct": None}]
    )
    assert (ok, graded) == (1, 2)


def test_disagreement_is_a_changed_grade() -> None:
    left = [
        {"question_id": "a", "correct": True},
        {"question_id": "b", "correct": None},
        {"question_id": "c", "correct": False},
    ]
    right = [
        {"question_id": "a", "correct": False},
        {"question_id": "b", "correct": None},
        {"question_id": "d", "correct": True},
    ]
    assert disagreement(left, right) == (1, 2)


def test_the_split_names_both_halves() -> None:
    halves = load_halves()
    assert halves["movies_4"] == "dev"
    assert halves["shipping"] == "holdout"
    assert set(halves.values()) == {"dev", "holdout"}


def test_the_report_prints_both_halves_and_disagreement(tmp_path: Path, capsys) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "test_final.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"question_id": "q1", "db_id": "movies_4", "sql_rename": "select 1"}),
                json.dumps({"question_id": "q2", "db_id": "shipping", "sql_rename": "select 1"}),
            ]
        ),
        encoding="utf-8",
    )
    artifact = tmp_path / "a.jsonl"
    other = tmp_path / "b.jsonl"
    artifact.write_text(
        "\n".join(
            [
                json.dumps({"question_id": "q1", "correct": True}),
                json.dumps({"question_id": "q2", "correct": False}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    other.write_text(
        json.dumps({"question_id": "q1", "correct": False}) + "\n"
        + json.dumps({"question_id": "q2", "correct": False}) + "\n",
        encoding="utf-8",
    )

    def coverage(rows, _gold):
        return {"all_gold_tables_retrieved_tables": 1.0 if rows else 0.0}

    args = type("Args", (), {"dataset": dataset})()

    print_report(artifact, args, coverage, against=other)
    out = capsys.readouterr().out
    assert "EX" in out
    assert "dev EX" in out
    assert "holdout EX" in out
    assert "dev coverage" in out
    assert "holdout coverage" in out
    assert "disagreement" in out and "1/2 = 0.500" in out
