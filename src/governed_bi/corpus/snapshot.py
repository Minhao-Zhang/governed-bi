"""Copy a corpus tree aside and put that copy back.

``restore`` removes files the snapshot lacks. A tree must hold a YAML asset before it can be replaced. Nested paths are
refused. The new tree is copied before the live one is removed. ``snapshot`` may replace an empty directory.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from governed_bi.corpus.hash import corpus_content_hash
from governed_bi.corpus.identity import corpus_files

__all__ = ["snapshot", "restore", "drifted"]

#: A content hash succeeds on any directory. A corpus here means it holds a YAML asset.
_ASSET_SUFFIXES: tuple[str, ...] = (".yaml", ".yml")


def _refuse_nesting(a: Path, b: Path) -> None:
    """Refuse paths where either contains the other, or they are the same path.

    A snapshot inside the tree it copies is removed when that tree is replaced.
    """
    left, right = a.resolve(), b.resolve()
    if left == right or left in right.parents or right in left.parents:
        raise ValueError(
            f"{a} and {b} are the same tree or nested one inside the other. A snapshot that "
            "lives inside the corpus it snapshots is deleted along with it, so the operation "
            "that exists to make a change undoable would make it unrecoverable. Put the "
            "snapshot beside the corpus, not in it."
        )


def _identify_corpus(root: Path) -> str:
    """Content hash of a tree this module will replace, or raise.

    A directory with no YAML asset is refused: hashing it is not identification.
    """
    digest = corpus_content_hash(root)
    if not corpus_files(root, suffixes=_ASSET_SUFFIXES):
        raise ValueError(
            f"{root} hashes but holds no {' / '.join(_ASSET_SUFFIXES)} asset, so it is not "
            "identifiable as a corpus. Refusing rather than deleting it: a hash succeeds on any "
            "directory, and this operation removes everything the snapshot does not have."
        )
    return digest


def snapshot(root: Path, dest: Path) -> str:
    """Copy ``root`` to ``dest`` and return the content hash.

    An existing ``dest`` must be empty or itself a corpus. A file raises ``NotADirectoryError``.
    """
    _refuse_nesting(dest, root)
    if dest.exists():
        if not dest.is_dir():
            raise NotADirectoryError(
                f"{dest} exists and is not a directory, so it is not a snapshot this module "
                "wrote and not one it will replace."
            )
        if any(dest.iterdir()):
            _identify_corpus(dest)
        shutil.rmtree(dest)
    shutil.copytree(root, dest)
    return corpus_content_hash(root)


def restore(snap: Path, root: Path) -> None:
    """Replace ``root`` with ``snap``, deleting files the snapshot lacks.

    Both must be corpora, and neither may contain the other. The copy is staged before ``root`` is removed.
    """
    if not snap.is_dir():
        raise FileNotFoundError(f"no snapshot at {snap}")
    _refuse_nesting(snap, root)
    _identify_corpus(snap)
    if root.exists():
        _identify_corpus(root)

    staged = root.with_name(root.name + ".restoring")
    replaced = root.with_name(root.name + ".replaced")
    for scratch in (staged, replaced):
        if scratch.exists():
            shutil.rmtree(scratch)

    shutil.copytree(snap, staged)
    if root.exists():
        root.rename(replaced)
    staged.rename(root)
    if replaced.exists():
        shutil.rmtree(replaced)


def drifted(root: Path, expected: str) -> bool:
    return corpus_content_hash(root) != expected
