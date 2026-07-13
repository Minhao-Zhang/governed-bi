"""Tests for the BIRD-Obfuscation loader (D14, WS4-partial).

Hermetic: a tiny fixture jsonl is written to ``tmp_path`` and never touches the
real ``../BIRD-Data-Obfuscation`` checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governed_bi.eval import EvalItem, available_dbs, load_bird_items

# Three rows spanning two db_ids, each carrying the real BIRD key set.
_ROWS = [
    {
        "db_id": "beer_factory",
        "question": "What is the total revenue?",
        "question_id": 0,
        "difficulty": "simple",
        "evidence": "",
        "sql_base": "SELECT SUM(pp) FROM t",
        "sql_rename": "SELECT SUM(PurchasePrice) FROM decoy",
        "sql_sqlite": 'SELECT SUM(PurchasePrice) FROM "transaction"',
    },
    {
        "db_id": "beer_factory",
        "question": "How many customers are there?",
        "question_id": 1,
        "difficulty": "simple",
        "evidence": "",
        "sql_base": "SELECT COUNT(*) FROM c",
        "sql_rename": "SELECT COUNT(*) FROM decoy_c",
        "sql_sqlite": "SELECT COUNT(*) FROM customers",
    },
    {
        "db_id": "movie_platform",
        "question": "How many movies are rated?",
        "question_id": 2,
        "difficulty": "moderate",
        "evidence": "",
        "sql_base": "SELECT COUNT(*) FROM m",
        "sql_rename": "SELECT COUNT(*) FROM decoy_m",
        "sql_sqlite": "SELECT COUNT(*) FROM ratings",
    },
]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows) + "\n",  # trailing blank line
        encoding="utf-8",
    )


@pytest.fixture
def dataset_dir(tmp_path: Path) -> Path:
    _write_jsonl(tmp_path / "test_final.jsonl", _ROWS)
    # A distinct train split so a split mix-up would be caught.
    _write_jsonl(
        tmp_path / "train_final.jsonl",
        [{**_ROWS[0], "question": "TRAIN: total revenue?", "question_id": 100}],
    )
    return tmp_path


def test_filters_by_db_id_and_maps_question_and_sql(dataset_dir: Path):
    items = load_bird_items(dataset_dir, "beer_factory")
    assert len(items) == 2
    assert items[0].question == "What is the total revenue?"
    assert items[0].sql == 'SELECT SUM(PurchasePrice) FROM "transaction"'
    assert items[0].question_id == "0"
    assert items[0].difficulty == "simple"
    assert items[1].question == "How many customers are there?"
    assert items[1].sql == "SELECT COUNT(*) FROM customers"
    # It maps sql_sqlite (the un-obfuscated gold), not sql_base / sql_rename.
    assert all(it.sql for it in items)
    assert not any("decoy" in it.sql for it in items)


def test_gold_sql_field_selects_sql_rename(dataset_dir: Path):
    items = load_bird_items(dataset_dir, "beer_factory", gold_sql_field="sql_rename")
    assert [it.sql for it in items] == [
        "SELECT SUM(PurchasePrice) FROM decoy",
        "SELECT COUNT(*) FROM decoy_c",
    ]



def test_filters_out_other_db_ids(dataset_dir: Path):
    items = load_bird_items(dataset_dir, "movie_platform")
    assert [it.question for it in items] == ["How many movies are rated?"]
    assert items[0].sql == "SELECT COUNT(*) FROM ratings"


def test_honors_split(dataset_dir: Path):
    train = load_bird_items(dataset_dir, "beer_factory", split="train")
    assert [it.question for it in train] == ["TRAIN: total revenue?"]


def test_available_dbs(dataset_dir: Path):
    assert available_dbs(dataset_dir) == {"beer_factory", "movie_platform"}
    assert available_dbs(dataset_dir, split="train") == {"beer_factory"}


def test_unknown_db_id_yields_no_items(dataset_dir: Path):
    assert load_bird_items(dataset_dir, "nope") == []


def test_bad_split_raises_value_error(dataset_dir: Path):
    with pytest.raises(ValueError, match="split"):
        load_bird_items(dataset_dir, "beer_factory", split="dev")


def test_missing_file_raises_file_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="test_final.jsonl"):
        load_bird_items(tmp_path, "beer_factory")


def test_row_missing_sql_sqlite_raises_value_error_naming_question_id(tmp_path: Path):
    bad = {k: v for k, v in _ROWS[0].items() if k != "sql_sqlite"}
    _write_jsonl(tmp_path / "test_final.jsonl", [bad])
    with pytest.raises(ValueError, match="question_id=0.*sql_sqlite"):
        load_bird_items(tmp_path, "beer_factory")
