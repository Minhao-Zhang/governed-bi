"""Save a corpus tree and put that copy back.

The content hash detects a change. It does not restore the previous tree. ``restore`` also deletes files the snapshot
never had.
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
    root = _corpus(tmp_path / "corpus")
    captured = snapshot(root, tmp_path / "snap")
    (root / "b.yaml").write_text("id: b\nkind: term\n")

    restore(tmp_path / "snap", root)

    assert corpus_content_hash(root) == captured
    assert not (root / "b.yaml").exists()


def test_restore_removes_a_file_the_snapshot_did_not_have(tmp_path: Path) -> None:
    """An overwrite-only restore would leave a new file in place."""
    root = _corpus(tmp_path / "corpus")
    snapshot(root, tmp_path / "snap")
    (root / "draft.yaml").write_text("id: draft\nkind: term\nstatus: proposed\n")

    restore(tmp_path / "snap", root)

    assert not (root / "draft.yaml").exists()


def test_restore_refuses_a_directory_that_is_not_a_corpus(tmp_path: Path) -> None:
    """A content hash succeeds on any directory, so a tree with no YAML asset is refused."""
    snap = _corpus(tmp_path / "snap")
    victim = tmp_path / "my_documents"
    victim.mkdir()
    (victim / "IMPORTANT.txt").write_text("not a corpus")

    with pytest.raises(ValueError, match="not identifiable as a corpus"):
        restore(snap, victim)

    assert (victim / "IMPORTANT.txt").read_text() == "not a corpus"
    assert not (victim / "a.yaml").exists()


def test_snapshot_refuses_a_destination_that_is_not_a_corpus(tmp_path: Path) -> None:
    """``snapshot`` deletes ``dest``, so a destination that is not a corpus is refused too."""
    root = _corpus(tmp_path / "corpus")
    victim = tmp_path / "my_documents"
    victim.mkdir()
    (victim / "IMPORTANT.txt").write_text("not a corpus")
    (victim / "sub").mkdir()
    (victim / "sub" / "taxes.pdf").write_text("also not a corpus")

    with pytest.raises(ValueError, match="not identifiable as a corpus"):
        snapshot(root, victim)

    assert (victim / "IMPORTANT.txt").read_text() == "not a corpus"
    assert (victim / "sub" / "taxes.pdf").exists()
    assert not (victim / "a.yaml").exists()


def test_snapshot_replaces_a_destination_that_is_itself_a_corpus(tmp_path: Path) -> None:
    """A second snapshot into the same path is allowed: that destination is itself a corpus."""
    root = _corpus(tmp_path / "corpus")
    snapshot(root, tmp_path / "snap")
    (root / "b.yaml").write_text("id: b\nkind: term\n")

    captured = snapshot(root, tmp_path / "snap")

    assert captured == corpus_content_hash(root)
    assert (tmp_path / "snap" / "b.yaml").exists()


def test_snapshot_accepts_an_empty_destination_directory(tmp_path: Path) -> None:
    """An empty directory is allowed here because it holds nothing to lose."""
    root = _corpus(tmp_path / "corpus")
    dest = tmp_path / "snap"
    dest.mkdir()

    captured = snapshot(root, dest)

    assert captured == corpus_content_hash(root)
    assert (dest / "a.yaml").exists()


def test_snapshot_refuses_a_destination_that_is_an_existing_file(tmp_path: Path) -> None:
    """A file is refused here, rather than by ``rmtree``."""
    root = _corpus(tmp_path / "corpus")
    dest = tmp_path / "snap"
    dest.write_text("i am a file")

    with pytest.raises(NotADirectoryError, match="not a directory"):
        snapshot(root, dest)

    assert dest.read_text() == "i am a file"


def test_a_snapshot_may_not_live_inside_the_corpus_it_snapshots(tmp_path: Path) -> None:
    """A snapshot inside the tree is removed when the tree is replaced."""
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
    """The staged copy is complete before ``root`` is renamed, so a failed swap leaves the original."""
    root = _corpus(tmp_path / "corpus")
    snapshot(root, tmp_path / "snap")
    (root / "b.yaml").write_text("id: b\nkind: term\n")
    original = corpus_content_hash(root)

    real_rename = Path.rename
    seen: dict[str, str] = {}

    def explode(self: Path, target: object) -> None:
        # First rename is ``root`` to ``root.replaced``. The staged copy must already be complete.
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
