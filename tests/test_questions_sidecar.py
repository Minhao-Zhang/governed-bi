"""questions.jsonl side-car + gold join (N15.4)."""

from __future__ import annotations

import json
from pathlib import Path

from governed_bi.eval.analysis import (
    load_gold_sql,
    load_questions_sidecar,
    resolve_gold_sql,
    write_questions_sidecar,
)


def test_write_and_load_questions_sidecar(tmp_path: Path):
    path = write_questions_sidecar(
        tmp_path,
        [
            {
                "question_id": "q1",
                "db_id": "address",
                "question": "Where is Arecibo?",
                "gold_sql": "SELECT 1",
                "split": "test",
            },
            {
                "question_id": "q1",  # dedupe
                "db_id": "address",
                "question": "dup",
                "gold_sql": "SELECT 2",
            },
            {
                "question_id": "q2",
                "db_id": "world",
                "question": "Capital?",
                "gold_sql": "SELECT 3",
            },
        ],
    )
    assert path == tmp_path / "questions.jsonl"
    loaded = load_questions_sidecar(tmp_path)
    assert set(loaded) == {"q1", "q2"}
    assert loaded["q1"]["question"] == "Where is Arecibo?"
    assert loaded["q1"]["gold_sql"] == "SELECT 1"


def test_resolve_gold_sql_prefers_sidecar(tmp_path: Path):
    bird = tmp_path / "bird"
    dataset = bird / "eval_dataset"
    dataset.mkdir(parents=True)
    (dataset / "test_final.jsonl").write_text(
        json.dumps({"question_id": "q1", "sql_rename": "SELECT bird"})
        + "\n"
        + json.dumps({"question_id": "q2", "sql_rename": "SELECT bird2"})
        + "\n",
        encoding="utf-8",
    )
    run = tmp_path / "run"
    run.mkdir()
    write_questions_sidecar(
        run,
        [{"question_id": "q1", "gold_sql": "SELECT side", "question": "q"}],
    )
    gold = resolve_gold_sql(run, bird, split="test")
    assert gold["q1"] == "SELECT side"
    assert gold["q2"] == "SELECT bird2"


def test_resolve_gold_sql_falls_back_without_sidecar(tmp_path: Path):
    bird = tmp_path / "bird"
    dataset = bird / "eval_dataset"
    dataset.mkdir(parents=True)
    (dataset / "test_final.jsonl").write_text(
        json.dumps({"question_id": "q1", "sql_rename": "SELECT bird"}) + "\n",
        encoding="utf-8",
    )
    run = tmp_path / "run"
    run.mkdir()
    assert load_questions_sidecar(run) == {}
    gold = resolve_gold_sql(run, bird, split="test")
    assert gold == load_gold_sql(bird, split="test")
    assert gold["q1"] == "SELECT bird"
