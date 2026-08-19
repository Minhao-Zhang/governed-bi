"""``corpus_content_hash`` says a corpus changed. It cannot say what it was before.

Experiment 008 built its control arm by copying a tree by hand and confirming with
``diff -rq``. That worked once. It does not survive ``enable_clarification_to_draft``, whose
own declaration says it "changes the corpus on disk between two turns of the SAME run" -- a
live arm now mutates its own treatment identity while running.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


# ── the three guards on the one operation here that can destroy data ───────────


def test_restore_refuses_a_directory_that_is_not_a_corpus(tmp_path: Path) -> None:
    """``restore`` deleted whatever path it was handed. Measured: a directory holding only
    ``IMPORTANT.txt`` and no corpus at all was silently removed, then replaced with the
    snapshot's contents. Hashing is not the check that prevents this -- ``corpus_content_hash``
    succeeds on any directory -- so the tree must be identifiable as a corpus."""
    snap = _corpus(tmp_path / "snap")
    victim = tmp_path / "my_documents"
    victim.mkdir()
    (victim / "IMPORTANT.txt").write_text("not a corpus")

    with pytest.raises(ValueError, match="not identifiable as a corpus"):
        restore(snap, victim)

    assert (victim / "IMPORTANT.txt").read_text() == "not a corpus"
    assert not (victim / "a.yaml").exists()


def test_a_snapshot_may_not_live_inside_the_corpus_it_snapshots(tmp_path: Path) -> None:
    """``snapshot(root, root / "snap")`` was accepted, which puts the only backup inside the tree
    ``restore`` later deletes -- so the operation that exists to make a change undoable made it
    unrecoverable instead. Both nesting directions are refused, and so is the same path twice."""
    root = _corpus(tmp_path / "corpus")

    with pytest.raises(ValueError, match="nested"):
        snapshot(root, root / "snap")
    with pytest.raises(ValueError, match="nested"):
        snapshot(root, root)

    assert not (root / "snap").exists()

    outer = _corpus(tmp_path / "outer")
    with pytest.raises(ValueError, match="nested"):
        restore(outer, outer / "inner")


def test_restore_copies_before_it_deletes(tmp_path: Path) -> None:
    """Delete-then-copy leaves a window with the corpus in neither place, and the interruption
    that lands in it is unrecoverable. Copy-then-swap means the replacement exists in full before
    anything is removed, so an interruption leaves ``root`` or ``root.replaced`` intact.

    Driven by making the swap itself fail: the staged copy must already be complete, and the
    original must still be readable, at the moment the operation dies."""
    root = _corpus(tmp_path / "corpus")
    snapshot(root, tmp_path / "snap")
    (root / "b.yaml").write_text("id: b\nkind: term\n")
    original = corpus_content_hash(root)

    real_rename = Path.rename
    seen: dict[str, str] = {}

    def explode(self: Path, target: object) -> None:
        # Fires on the first rename, which is `root -> root.replaced`. By then the staged copy
        # must be complete: that is the property this test exists to pin.
        staged = tmp_path / "corpus.restoring"
        seen["staged_hash"] = corpus_content_hash(staged)
        seen["root_hash"] = corpus_content_hash(root)
        raise OSError("interrupted mid-swap")

    Path.rename = explode  # type: ignore[method-assign]
    try:
        with pytest.raises(OSError, match="interrupted"):
            restore(tmp_path / "snap", root)
    finally:
        Path.rename = real_rename  # type: ignore[method-assign]

    assert seen["staged_hash"] != seen["root_hash"], "the staged copy was not the snapshot"
    assert seen["root_hash"] == original, "root was modified before the copy completed"
    assert corpus_content_hash(root) == original, "root did not survive the interruption"
