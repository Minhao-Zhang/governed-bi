"""Capture a corpus tree and put it back.

``corpus_content_hash`` is a detector: it says the treatment identity changed. It cannot say
what the tree was before, and it cannot undo the change. Two arms that must differ by exactly
one thing need both halves.

**``restore`` deletes as well as overwrites.** A draft written by
``enable_clarification_to_draft`` is a file the snapshot never had, and a restore that only
copied forward would leave it in place -- the second arm would then silently carry the first
arm's treatment, which is the failure mode a control arm exists to prevent.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from governed_bi.corpus.hash import corpus_content_hash

__all__ = ["snapshot", "restore", "drifted"]


def snapshot(root: Path, dest: Path) -> str:
    """Copy ``root`` to ``dest`` and return the content hash captured."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(root, dest)
    return corpus_content_hash(root)


def restore(snap: Path, root: Path) -> None:
    """Replace ``root`` with ``snap``, removing anything the snapshot did not have."""
    if not snap.is_dir():
        raise FileNotFoundError(f"no snapshot at {snap}")
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(snap, root)


def drifted(root: Path, expected: str) -> bool:
    """Whether ``root``'s content hash has moved away from ``expected``."""
    return corpus_content_hash(root) != expected
