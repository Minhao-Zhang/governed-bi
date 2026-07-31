"""CLI entry points for error_taxonomy and sql_diff (N15.1).

Synthetic run_dir + bird gold only — no fixed2 run dependency.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governed_bi.eval import error_taxonomy, sql_diff


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Tiny two-arm run with one correct and one wrong row."""
    bird = tmp_path / "bird"
    _write_jsonl(
        bird / "eval_dataset" / "test_final.jsonl",
        [
            {
                "question_id": "q_ok",
                "sql_rename": "SELECT customers.name FROM customers",
            },
            {
                "question_id": "q_wrong",
                "sql_rename": (
                    "SELECT customers.name FROM customers "
                    "JOIN orders ON customers.id = orders.cid"
                ),
            },
        ],
    )
    run = tmp_path / "run"
    _write_jsonl(
        run / "generations.curated_sme.jsonl",
        [
            {
                "question_id": "q_ok",
                "db_id": "shop",
                "split": "test",
                "correct": True,
                "routed_hit": True,
                "pick_hit": True,
                "generated_sql": "SELECT customers.name FROM customers",
            },
            {
                "question_id": "q_wrong",
                "db_id": "shop",
                "split": "test",
                "correct": False,
                "routed_hit": True,
                "pick_hit": True,
                # Wrong table in the projection — structural mismatch.
                "generated_sql": (
                    "SELECT orders.name FROM customers "
                    "JOIN orders ON customers.id = orders.cid"
                ),
            },
        ],
    )
    _write_jsonl(
        run / "generations.baseline.jsonl",
        [
            {
                "question_id": "q_ok",
                "db_id": "shop",
                "split": "test",
                "correct": True,
                "routed_hit": True,
                "pick_hit": True,
                "generated_sql": "SELECT customers.name FROM customers",
            },
        ],
    )
    return run, bird


def test_error_taxonomy_main_prints_summary_and_writes_out(tmp_path, capsys):
    run, bird = _fixture(tmp_path)
    out = tmp_path / "taxonomy.json"
    error_taxonomy.main(
        [
            str(run),
            "--bird-dir",
            str(bird),
            "--arm",
            "curated_sme",
            "--out",
            str(out),
        ]
    )
    printed = json.loads(capsys.readouterr().out.split("\nwrote ")[0])
    written = json.loads(out.read_text(encoding="utf-8"))
    assert printed == written
    assert printed["split"] == "test"
    assert printed["filters"]["arm"] == "curated_sme"
    arm = printed["arms"]["curated_sme"]
    assert arm["summary"]["n"] == 2
    assert arm["summary"]["n_wrong"] == 1
    assert "wrong_projection" in arm["summary"]["by_error_primary"]
    # Two filtered rows <= cap → per-row attributions included.
    assert len(arm["rows"]) == 2


def test_error_taxonomy_main_filters_question_id(tmp_path, capsys):
    run, bird = _fixture(tmp_path)
    error_taxonomy.main(
        [
            str(run),
            "--bird-dir",
            str(bird),
            "--arm",
            "curated_sme",
            "--question-id",
            "q_wrong",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    arm = report["arms"]["curated_sme"]
    assert arm["summary"]["n"] == 1
    assert arm["summary"]["n_wrong"] == 1
    assert [r["question_id"] for r in arm["rows"]] == ["q_wrong"]
    assert arm["rows"][0]["error_primary"] == "wrong_projection"


def test_error_taxonomy_main_unknown_arm_exits(tmp_path):
    run, bird = _fixture(tmp_path)
    with pytest.raises(SystemExit, match="unknown arm"):
        error_taxonomy.main(
            [str(run), "--bird-dir", str(bird), "--arm", "no_such_arm"]
        )


def test_sql_diff_main_prints_incidence_and_writes_out(tmp_path, capsys):
    run, bird = _fixture(tmp_path)
    out = tmp_path / "diff.json"
    sql_diff.main(
        [
            str(run),
            "--bird-dir",
            str(bird),
            "--arm",
            "curated_sme",
            "--db",
            "shop",
            "--out",
            str(out),
        ]
    )
    printed = json.loads(capsys.readouterr().out.split("\nwrote ")[0])
    written = json.loads(out.read_text(encoding="utf-8"))
    assert printed == written
    arm = printed["arms"]["curated_sme"]
    assert arm["n"] == 2
    assert arm["n_comparable"] == 2
    assert arm["dimension_incidence"].get("projection") == 1
    assert len(arm["rows"]) == 2


def test_sql_diff_main_question_id_emits_row_diff(tmp_path, capsys):
    run, bird = _fixture(tmp_path)
    sql_diff.main(
        [
            str(run),
            "--bird-dir",
            str(bird),
            "--arm",
            "curated_sme",
            "--question-id",
            "q_wrong",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    rows = report["arms"]["curated_sme"]["rows"]
    assert len(rows) == 1
    assert rows[0]["question_id"] == "q_wrong"
    assert "projection" in rows[0]["diff"]["mismatched"]
