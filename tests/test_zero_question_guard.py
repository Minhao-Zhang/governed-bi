"""Schemas with zero questions must not look built-but-unscored or corrupt census.

eval-rebuild §4 deferred this guard: after rescreening, a schema can still build while
its split has no rows. Leaving it in ``built_dbs`` inflated corpus census / router
candidates against a graded denominator that never included it. The fix quarantines
those schemas explicitly (``dbs_zero_questions``) the same way curator-error attrition
is named rather than vanished.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from governed_bi.eval.index import quotable, record_for_run
from governed_bi.eval.run_datalake import (
    _load_built_corpus,
    _pooled_items,
    _prepare_scored_pool,
    _quarantine_zero_question_schemas,
    run_datalake,
)


def _write_split(dataset_dir: Path, split: str, rows: list[dict]) -> None:
    dataset_dir.mkdir(parents=True, exist_ok=True)
    path = dataset_dir / f"{split}_final.jsonl"
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in rows),
        encoding="utf-8",
    )


def _item_row(db_id: str, qid: str) -> dict:
    return {
        "db_id": db_id,
        "question_id": qid,
        "question": f"q for {qid}",
        "sql_rename": f"SELECT 1 /* {qid} */",
    }


def test_zero_question_schema_leaves_the_pool_and_is_named(tmp_path):
    """The whole fix in one assertion: empty schema leaves ``built``, and is named."""
    dataset = tmp_path / "eval_dataset"
    _write_split(
        dataset,
        "test",
        [
            _item_row("beer_factory", "q1"),
            _item_row("beer_factory", "q2"),
            # empty_db has no rows — synthetic empty after rescreen.
        ],
    )
    built = ["beer_factory", "empty_db", "also_empty"]
    pairs = _pooled_items(dataset, built, limit=None, split="test")

    servable, empty = _quarantine_zero_question_schemas(built, pairs)

    assert servable == ["beer_factory"]
    assert empty == ["empty_db", "also_empty"]
    assert all(db == "beer_factory" for _item, db in pairs)


def test_order_of_surviving_pool_is_preserved(tmp_path):
    dataset = tmp_path / "eval_dataset"
    _write_split(
        dataset,
        "test",
        [_item_row("zebra", "z1"), _item_row("monkey", "m1")],
    )
    built = ["zebra", "apple", "monkey"]
    pairs = _pooled_items(dataset, built, limit=None, split="test")
    servable, empty = _quarantine_zero_question_schemas(built, pairs)
    assert servable == ["zebra", "monkey"]
    assert empty == ["apple"]


def test_all_schemas_empty_aborts(tmp_path):
    dataset = tmp_path / "eval_dataset"
    _write_split(dataset, "test", [])  # no questions for anyone
    built = ["a", "b"]
    pairs = _pooled_items(dataset, built, limit=None, split="test")
    with pytest.raises(RuntimeError, match="zero questions"):
        _quarantine_zero_question_schemas(built, pairs)


def test_clean_pool_changes_nothing(tmp_path):
    dataset = tmp_path / "eval_dataset"
    _write_split(
        dataset,
        "test",
        [_item_row("a", "a1"), _item_row("b", "b1")],
    )
    built = ["a", "b"]
    pairs = _pooled_items(dataset, built, limit=None, split="test")
    servable, empty = _quarantine_zero_question_schemas(built, pairs)
    assert servable == built
    assert empty == []


def _run_dir(tmp_path, **summary_extra):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_schema_version": 1,
                "model": "gpt-5.6-luna",
                "split": "test",
                "route_top_k": 10,
                "route_llm_pick": True,
                "grade_semantic_failures": True,
            }
        ),
        encoding="utf-8",
    )
    arm = {
        "n": 1500,
        "ex_lenient": 0.33,
        "crash_rate": 0.0,
        "n_correct_with_empty_gold": 0,
        "n_correct_and_pred_has_no_from": 0,
        "n_correct_and_zero_table_overlap": 0,
    }
    summary = {
        "mode": "datalake",
        "split": "test",
        "n_questions": 1500,
        "n_dbs_built": 56,
        "n_dbs_requested": 57,
        "arms": {"baseline": dict(arm), "curated": dict(arm)},
        "build_errors": {},
        "curator_errors": {},
    }
    summary.update(summary_extra)
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return run_dir


def test_zero_question_schema_reaches_ledger_and_blocks_quoting(tmp_path):
    record = record_for_run(
        _run_dir(tmp_path, dbs_zero_questions=["ghost_schema"], n_dbs_requested=57)
    )
    assert record["dbs_zero_questions"] == ["ghost_schema"]
    ok, reasons = quotable(record)
    assert not ok
    joined = " | ".join(reasons)
    assert "zero questions" in joined
    assert "ghost_schema" in joined
    assert "1 of 57" in joined


def test_a_run_with_no_empty_schemas_is_still_quotable(tmp_path):
    record = record_for_run(_run_dir(tmp_path, dbs_zero_questions=[]))
    ok, reasons = quotable(record)
    assert ok, reasons


def test_absent_key_is_not_accused(tmp_path):
    """Predates the guard — absence is not a hidden empty-schema list."""
    record = record_for_run(_run_dir(tmp_path))
    assert record["dbs_zero_questions"] == []
    ok, reasons = quotable(record)
    assert ok, reasons


# --------------------------------------------------------------------------- #
# Driver wiring — deleting the call site must fail these, not only unit tests.
# --------------------------------------------------------------------------- #


def test_prepare_scored_pool_names_empty_schemas_and_scopes_leakage(tmp_path):
    """The helper the driver calls: empty schema leaves the pool and leakage."""
    dataset = tmp_path / "eval_dataset"
    _write_split(
        dataset,
        "test",
        [_item_row("beer_factory", "q1"), _item_row("beer_factory", "q2")],
    )
    # Train rows for the empty test schema — leakage used to count these while
    # built_dbs dropped the schema (B4).
    _write_split(dataset, "train", [_item_row("ghost", "train_g1")])

    built = ["beer_factory", "ghost"]
    servable, pairs, empty, leakage = _prepare_scored_pool(
        dataset, built, limit=None, split="test"
    )

    assert servable == ["beer_factory"]
    assert empty == ["ghost"]
    assert all(db == "beer_factory" for _item, db in pairs)
    # Leakage is over the servable pool only — ghost's train id is not in n_train_ids.
    assert leakage["n_train_ids"] == 0
    assert leakage["n_test_ids"] == 2


def test_driver_wires_prepare_scored_pool_before_corpora_load():
    """Deleting ``_prepare_scored_pool`` from ``run_datalake`` must fail CI.

    Unit tests on ``_quarantine_zero_question_schemas`` stay green if the call
    site is removed; this pins the driver path that writes ``dbs_zero_questions``.
    """
    src = inspect.getsource(run_datalake)
    prepare = src.index("_prepare_scored_pool(")
    load = src.index("_load_built_corpus(")
    assert prepare < load, (
        "zero-question quarantine must run before corpora / census / routing, "
        "or empty schemas re-enter the pool as built-but-unscored"
    )
    assert "dbs_zero_questions" in src


def test_load_built_corpus_does_not_reintroduce_quarantined_schemas(tmp_path):
    """Census half of eval-rebuild §4: corpora load only the servable ``built`` list.

    Sibling of ``test_only_the_dbs_being_scored_enter_the_served_corpus`` — kept here
    so the zero-question guard suite covers both halves of the §4 requirement.
    """
    from governed_bi.corpus import load_corpus, write_corpus
    from governed_bi.corpus.schemas import Column, LogicalType, TableAsset

    def _table(schema: str) -> TableAsset:
        return TableAsset(
            id=f"tbl_{schema}_orders",
            schema=schema,
            physical_name="orders",
            columns=[
                Column(
                    physical_name="order_id",
                    physical_type="INTEGER",
                    logical_type=LogicalType.integer,
                    nullable=True,
                    is_unique=False,
                )
            ],
        )

    root = tmp_path / "corpus_baseline"
    write_corpus(root, "beer_factory", [_table("beer_factory")])
    write_corpus(root, "ghost", [_table("ghost")])

    assert {t.schema for t in load_corpus(root).tables()} == {"beer_factory", "ghost"}
    scoped = _load_built_corpus(root, ["beer_factory"])
    assert {t.schema for t in scoped.tables()} == {"beer_factory"}
