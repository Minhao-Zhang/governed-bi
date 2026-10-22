"""Resume keys that are constant ``None`` cannot disagree, so the check compares each key with itself."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from governed_bi.eval.provenance import (
    git_provenance,
    harness_settings,
    scope_identity,
    short_digest,
)
from governed_bi.registry.settings import setting_names

REPO = Path(__file__).resolve().parents[2]


def _settings(**over):
    args = {
        "repo": REPO,
        "schemas": ["beer_factory", "works_cycles"],
        "question_ids": ["train_1", "train_2", "train_3"],
        "dataset_file": Path("/data/eval_dataset/test_final.jsonl"),
        "serve_workers": 10,
    }
    args.update(over)
    return harness_settings(**args)


# ── the wire is real, not a name that happens to appear ──────────────────────


def test_every_key_the_harness_writes_is_a_declared_setting() -> None:
    """An undeclared key is outside every comparison, which is what a stray model name did."""
    undeclared = sorted(set(_settings()) - setting_names())
    assert not undeclared, f"{undeclared} are written but not declared in registry/settings.py"


def test_the_harness_writes_a_value_for_every_resume_drift_key_it_can_answer() -> None:
    """A digest implies a dirty tree. A dirty tree of only untracked files has an empty diff."""
    settings = _settings()
    written = {
        "git_sha",
        "diff_sha256",
        "working_tree_dirty",
        "serve_workers",
    } & set(settings)
    assert {"git_sha", "diff_sha256", "working_tree_dirty", "serve_workers"} <= written
    assert "build_workers" not in settings

    for name in written - {"diff_sha256"}:
        assert settings[name] is not None, f"{name} is written and unmeasured"

    if settings["diff_sha256"] is not None:
        assert settings["working_tree_dirty"] is True, (
            "diff_sha256 digests a non-empty `git diff HEAD`, so the tree cannot be clean. "
            "A digest beside working_tree_dirty=False means the two are read from different "
            "states, which is the resume-drift blend this key exists to catch."
        )


# ── git provenance ───────────────────────────────────────────────────────────


def test_the_git_sha_is_this_checkouts_head_and_not_a_placeholder() -> None:
    """Asserted against ``git`` itself. A digest of something else would satisfy any shape
    check and would still make the drift check compare a constant."""
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode != 0:  # pragma: no cover — not a checkout
        pytest.skip("not a git checkout")

    assert git_provenance(REPO)["git_sha"] == head.stdout.strip()


def test_a_dirty_tree_and_a_clean_one_record_different_diffs(tmp_path: Path) -> None:
    """``git_sha`` alone lets a resume across an uncommitted edit blend two harness versions
    with no check firing, which is the incident ``diff_sha256`` was declared for. Two states of
    one repository, so the assertion is on the response and not on a literal."""
    repo = tmp_path / "r"
    repo.mkdir()
    for cmd in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
    ):
        if subprocess.run(["git", "-C", str(repo), *cmd], capture_output=True).returncode:
            pytest.skip("git unavailable")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "one"], capture_output=True, check=True
    )

    clean = git_provenance(repo)
    assert clean["git_sha"]
    assert clean["working_tree_dirty"] is False
    assert clean["diff_sha256"] is None

    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    dirty = git_provenance(repo)
    assert dirty["git_sha"] == clean["git_sha"], "the commit did not move; only the tree did"
    assert dirty["working_tree_dirty"] is True
    assert dirty["diff_sha256"] is not None


def test_a_tree_that_is_not_a_checkout_records_nothing_rather_than_raising(
    tmp_path: Path,
) -> None:
    """A run must not die because the harness is a tarball, and an unanswerable question must
    read as unmeasured rather than as a value two rows can agree on."""
    assert git_provenance(tmp_path) == {
        "git_sha": None,
        "git_main_sha": None,
        "working_tree_dirty": None,
        "diff_sha256": None,
    }


# ── scope ────────────────────────────────────────────────────────────────────


def test_the_scope_digest_is_the_set_and_not_its_order_or_its_count() -> None:
    """``question_subset``'s register note is explicit that "a probe set's identity is not its
    count", and the order two questions came off a JSONL is an accident."""
    assert short_digest(["b", "a"]) == short_digest(["a", "b"])
    assert short_digest(["a", "b"]) != short_digest(["a", "c"])


def test_dropping_one_schema_moves_schemas_under_test() -> None:
    """The incident the setting names: "a schema dropped from one attempt leaves its YAML behind
    and competes as a router candidate for every other schema's questions"."""
    full = scope_identity(
        schemas=["a", "b", "c"], question_ids=["q1"], dataset_file=Path("d/test_final.jsonl")
    )
    dropped = scope_identity(
        schemas=["a", "b"], question_ids=["q1"], dataset_file=Path("d/test_final.jsonl")
    )
    assert full["schemas_under_test"] != dropped["schemas_under_test"]
    assert full["schemas_under_test"].startswith("3:")
    assert dropped["schemas_under_test"].startswith("2:")
    assert full["question_subset"] == dropped["question_subset"], "only the schemas moved"


def test_split_names_the_dataset_file_that_decided_the_scope() -> None:
    """Not the word "train": the ids in ``test_final.jsonl`` are BIRD ``train_*`` ids re-split
    by another repository, so answering "train or test" from them would be a claim about that
    repository's intent. The file name is a fact."""
    scope = scope_identity(
        schemas=["a"], question_ids=["q"], dataset_file=Path("x/eval_dataset/test_final.jsonl")
    )
    assert scope["split"] == "test_final"


# ── it reaches a measurement row ─────────────────────────────────────────────


def test_the_harness_settings_reach_the_measurement_row(tmp_path: Path) -> None:
    """The question's settings override the session's, which is why this goes through ``run_config``."""
    from governed_bi.datasource.sqlite import SqliteConnector
    from governed_bi.eval.configs import stub_config
    from governed_bi.eval.harness import run_config

    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE customers (id INTEGER)")
    conn.commit()
    conn.close()
    connector = SqliteConnector(db)

    settings = _settings()
    rows = run_config(
        [
            {
                "question_id": "q1",
                "question": "how many customers",
                "db_id": "main",
                "settings_resolved": settings,
            }
        ],
        stub_config(connector=connector),
    )

    recorded = rows[0]["settings_resolved"]
    assert recorded["serve_workers"] == 10
    assert recorded["git_sha"] == settings["git_sha"]
    assert recorded["question_subset"] == "3:" + short_digest(
        ["train_1", "train_2", "train_3"]
    )
    assert recorded["split"] == "test_final"
