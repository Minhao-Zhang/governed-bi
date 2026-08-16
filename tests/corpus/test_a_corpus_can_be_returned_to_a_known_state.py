"""``corpus_content_hash`` says a corpus changed. It cannot say what it was before.

Experiment 008 built its control arm by copying a tree by hand and confirming with
``diff -rq``. That worked once. It does not survive ``enable_clarification_to_draft``, whose
own declaration says it "changes the corpus on disk between two turns of the SAME run" -- a
live arm now mutates its own treatment identity while running.
"""

from __future__ import annotations

from pathlib import Path

from governed_bi.corpus.hash import corpus_content_hash
from governed_bi.corpus.snapshot import drifted, restore, snapshot


def _corpus(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.yaml").write_text("id: a\nkind: term\n")
    return root


def test_a_snapshot_records_the_hash_it_captured(tmp_path: Path) -> None:
    root = _corpus(tmp_path / "corpus")

    captured = snapshot(root, tmp_path / "snap")

    assert captured == corpus_content_hash(root)


def test_a_write_after_the_snapshot_is_visible_as_drift(tmp_path: Path) -> None:
    root = _corpus(tmp_path / "corpus")
    captured = snapshot(root, tmp_path / "snap")

    (root / "b.yaml").write_text("id: b\nkind: term\n")

    assert drifted(root, captured) is True


def test_restore_returns_the_tree_to_the_captured_hash(tmp_path: Path) -> None:
    """The half ``diff -rq`` never gave us: not detecting the change, undoing it."""
    root = _corpus(tmp_path / "corpus")
    captured = snapshot(root, tmp_path / "snap")
    (root / "b.yaml").write_text("id: b\nkind: term\n")

    restore(tmp_path / "snap", root)

    assert corpus_content_hash(root) == captured
    assert not (root / "b.yaml").exists()


def test_restore_removes_a_file_the_snapshot_did_not_have(tmp_path: Path) -> None:
    """A draft written mid-run is a *new* file. A restore that only overwrites would leave it
    behind and the next arm would inherit the previous arm's treatment."""
    root = _corpus(tmp_path / "corpus")
    snapshot(root, tmp_path / "snap")
    (root / "draft.yaml").write_text("id: draft\nkind: term\nstatus: proposed\n")

    restore(tmp_path / "snap", root)

    assert not (root / "draft.yaml").exists()
