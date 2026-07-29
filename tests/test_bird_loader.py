"""Tests for the BIRD-Obfuscation loader (D14, WS4-partial).

Hermetic: a tiny fixture jsonl is written to ``tmp_path`` and never touches the
real ``../BIRD-Data-Obfuscation`` checkout.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from governed_bi.eval import available_dbs, load_bird_items
from governed_bi.eval.bird_loader import load_cross_db_unanswerable

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


def test_cross_db_unanswerable_pulls_only_other_dbs(dataset_dir: Path):
    """The refuse-gate negative set must exclude the target db's questions and
    draw from the others (unanswerable by construction)."""
    qs = load_cross_db_unanswerable(dataset_dir, "beer_factory", k=10)
    assert qs == ["How many movies are rated?"]  # only the movie_platform row
    # And the reverse: target movie_platform -> the two beer_factory questions.
    other = load_cross_db_unanswerable(dataset_dir, "movie_platform", k=10)
    assert set(other) == {"What is the total revenue?", "How many customers are there?"}
    # k caps the result.
    assert len(load_cross_db_unanswerable(dataset_dir, "movie_platform", k=1)) == 1



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


# --------------------------------------------------------------------------- #
# Parse-once caching. The splits are ~9 MB / ~34 MB and a pooled run asks for them
# dozens of times (per db per split, plus the disjointness assertion, plus
# available_dbs), so this was tens of seconds of pure JSON parsing per run — paid
# again on every --resume-from.
# --------------------------------------------------------------------------- #


def test_a_split_is_parsed_once_across_many_calls(dataset_dir: Path, monkeypatch):
    from governed_bi.eval import bird_loader

    bird_loader.clear_split_cache()
    n_parses = {"n": 0}
    real = bird_loader._parse_rows

    def _counting(path):
        n_parses["n"] += 1
        return real(path)

    monkeypatch.setattr(bird_loader, "_parse_rows", _counting)

    load_bird_items(dataset_dir, "beer_factory")
    load_bird_items(dataset_dir, "movie_platform")
    available_dbs(dataset_dir)
    load_cross_db_unanswerable(dataset_dir, "beer_factory", k=1)

    assert n_parses["n"] == 1, "the test split should be parsed once, not per call"


def test_each_split_is_cached_separately(dataset_dir: Path, monkeypatch):
    from governed_bi.eval import bird_loader

    bird_loader.clear_split_cache()
    n_parses = {"n": 0}
    real = bird_loader._parse_rows
    monkeypatch.setattr(
        bird_loader,
        "_parse_rows",
        lambda p: (n_parses.__setitem__("n", n_parses["n"] + 1), real(p))[1],
    )

    load_bird_items(dataset_dir, "beer_factory", split="test")
    load_bird_items(dataset_dir, "beer_factory", split="train")
    load_bird_items(dataset_dir, "beer_factory", split="test")

    assert n_parses["n"] == 2, "one parse per split, not one per split per call"


def test_a_regenerated_split_is_not_served_from_cache(tmp_path: Path):
    """Serving a stale dataset would be a SCORING bug, not a caching one.

    Regenerating a split must invalidate. Keyed on (path, mtime_ns, size), so a file
    whose contents changed at all is a different entry.
    """
    from governed_bi.eval import bird_loader

    bird_loader.clear_split_cache()
    path = tmp_path / "test_final.jsonl"
    _write_jsonl(path, [_ROWS[0]])
    assert [it.question for it in load_bird_items(tmp_path, "beer_factory")] == [
        "What is the total revenue?"
    ]

    # Rewrite with different content (and a different size, so mtime granularity
    # cannot mask it).
    _write_jsonl(path, [_ROWS[0], _ROWS[1]])
    assert [it.question for it in load_bird_items(tmp_path, "beer_factory")] == [
        "What is the total revenue?",
        "How many customers are there?",
    ]


def test_cross_db_selection_is_unchanged_by_the_cache(dataset_dir: Path):
    """The round-robin is deterministic and draws only from OTHER dbs."""
    from governed_bi.eval import bird_loader

    bird_loader.clear_split_cache()
    first = load_cross_db_unanswerable(dataset_dir, "beer_factory", k=5)
    again = load_cross_db_unanswerable(dataset_dir, "beer_factory", k=5)
    assert first == again
    beer = {it.question for it in load_bird_items(dataset_dir, "beer_factory")}
    assert beer.isdisjoint(first)


def test_rows_without_a_db_id_are_ignored_everywhere(tmp_path: Path):
    from governed_bi.eval import bird_loader

    bird_loader.clear_split_cache()
    orphan = {k: v for k, v in _ROWS[0].items() if k != "db_id"}
    _write_jsonl(tmp_path / "test_final.jsonl", [_ROWS[0], orphan])
    assert available_dbs(tmp_path) == {"beer_factory"}
    assert len(load_bird_items(tmp_path, "beer_factory")) == 1


def test_description_dir_finds_both_bird_trees(tmp_path: Path):
    """BIRD splits its schemas across ``train_databases/`` and ``dev_databases/``.

    Hardcoding the train tree found nothing for the 11 dev-tree schemas
    (california_schools, financial, formula_1, superhero, ...) and built their SME
    arm's brief silently empty without failing — the arm looked measured and was
    not.
    """
    from governed_bi.eval.bird_loader import description_dir

    train = tmp_path / "data/train/train_databases/beer_factory/database_description"
    dev = tmp_path / "data/dev/dev_databases/california_schools/database_description"
    train.mkdir(parents=True)
    dev.mkdir(parents=True)

    assert description_dir(tmp_path, "beer_factory") == train
    assert description_dir(tmp_path, "california_schools") == dev
    assert description_dir(tmp_path, "not_a_schema") is None


def test_load_rename_map_reads_either_manifest_root(tmp_path: Path):
    from governed_bi.eval.bird_loader import load_rename_map

    assert load_rename_map(tmp_path, "beer_factory") == {}
    (tmp_path / "eval_dataset").mkdir()
    (tmp_path / "eval_dataset" / "schema_rename_map.json").write_text(
        json.dumps({"beer_factory": {"customers": "kunden"}}), encoding="utf-8"
    )
    assert load_rename_map(tmp_path, "beer_factory") == {"customers": "kunden"}
    assert load_rename_map(tmp_path, "absent_db") == {}


def test_load_rename_map_warns_instead_of_silently_not_translating(tmp_path: Path, caplog):
    """An empty map reverts every caller to un-translated identifiers.

    ``{}`` is returned for both "manifest absent" and "db not in manifest", and
    both are falsy, so the SME brief silently goes back to addressing BIRD's
    original names — the exact defect the rename map exists to fix, with the run
    completing and the numbers looking normal. Identity-rename dbs carry a full
    name -> same-name map, so an empty result is never "needs no translation".
    """
    from governed_bi.eval.bird_loader import load_rename_map

    with caplog.at_level(logging.WARNING, logger="governed_bi.eval"):
        assert load_rename_map(tmp_path, "beer_factory") == {}
    assert "schema_rename_map.json not found" in caplog.text

    (tmp_path / "eval_dataset").mkdir()
    (tmp_path / "eval_dataset" / "schema_rename_map.json").write_text(
        json.dumps({"beer_factory": {"customers": "kunden"}}), encoding="utf-8"
    )
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="governed_bi.eval"):
        assert load_rename_map(tmp_path, "absent_db") == {}
    assert "no entry in" in caplog.text
