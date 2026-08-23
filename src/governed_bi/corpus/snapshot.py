"""Capture a corpus tree and put it back.

``corpus_content_hash`` is a detector: it says the treatment identity changed. It cannot say
what the tree was before, and it cannot undo the change. Two arms that must differ by exactly
one thing need both halves.

**``restore`` deletes as well as overwrites.** Anything a run wrote into the tree -- a draft
asset, a ledger file, a scratch index -- is a file the snapshot never had, and a restore that only
copied forward would leave it in place. The second arm would then silently carry the first arm's
treatment, which is the failure mode a control arm exists to prevent. Measured here on 2026-08-19:
adding one file inside ``corpus_root`` moves ``corpus_content_hash``, because it passes no
``suffixes`` and digests everything that is not VCS bookkeeping.

**Which makes this the one module here that can destroy the user's data**, and the corpus is the
thing this product describes as "lose it and the answers go wrong". Three guards, one per way
that went wrong when it was measured:

* ``restore`` used to remove whatever path it was handed. A directory holding one
  ``IMPORTANT.txt`` and no corpus at all was silently deleted. It now refuses to delete a tree
  it cannot identify as a corpus.
* **``snapshot`` had the same hole and this paragraph used to imply it did not.** ``_identify_corpus``
  guarded ``restore`` only, while ``snapshot`` reached ``shutil.rmtree(dest)`` behind nothing but the
  nesting check -- so a ``dest`` that was not a corpus was removed without a question. Measured
  2026-08-23: pointed at a scratch directory holding unrelated files, it deleted them. Both
  functions now apply the same identification, and ``snapshot`` accepts one further case
  ``restore`` has no reason to -- an **empty** directory, which holds nothing to lose.
* ``snapshot(root, root / "snap")`` was accepted, which puts the only backup inside the tree
  ``restore`` later deletes. Nesting either way round is refused.
* the delete-then-copy order left no recoverable state in the window between the two. It is
  copy-then-swap now.

**A caller must never derive ``dest`` from a string it did not mint.** The guards above bound what
this module will delete; they do not make an attacker-chosen path safe, because a path that *is* a
corpus is exactly the path deleting is worst on. Compose a scratch directory from a run id this
process minted, not from anything that arrived over a socket.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from governed_bi.corpus.hash import corpus_content_hash
from governed_bi.corpus.identity import corpus_files

__all__ = ["snapshot", "restore", "drifted"]

#: What makes a tree identifiable as a corpus rather than as some directory. YAML typed assets
#: are the source of truth (D9), so a tree with none of them is not this module's to replace --
#: even though ``corpus_content_hash`` will happily digest any directory, which is exactly why
#: hashing alone is not the check.
_ASSET_SUFFIXES: tuple[str, ...] = (".yaml", ".yml")


def _refuse_nesting(a: Path, b: Path) -> None:
    """Refuse two paths where either contains the other, or they are the same path.

    ``snapshot(root, root / "snap")`` was accepted and is the case that matters: it puts the
    snapshot inside the tree ``restore`` later deletes, so the rmtree/copytree window has no
    recoverable state on either side of it -- the only backup is inside what was removed. The
    reverse nesting is the same defect seen from the other end.
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
    """The content hash of a tree this module is willing to replace, or raise.

    Two conditions, because either alone is too weak. ``corpus_content_hash`` raises only on a
    missing directory and otherwise digests whatever files it finds, so it succeeds on any
    directory at all -- which is how a directory holding one ``IMPORTANT.txt`` and no corpus was
    identified well enough to delete. A tree must therefore also hold at least one typed asset.
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
    """Copy ``root`` to ``dest`` and return the content hash captured.

    ``dest`` is replaced when it already exists, so it is held to the same identification
    ``restore`` holds its target to: it must be a corpus, or empty. The empty case is allowed
    here and not there because an empty directory has nothing to lose, and a caller that
    ``mkdir``s its scratch path before calling should not be refused for tidiness.

    Raises ``ValueError`` when ``dest`` exists, is non-empty, and is not identifiable as a
    corpus -- which is the case that deleted a scratch directory of unrelated files when it
    was measured. ``NotADirectoryError`` when ``dest`` is an existing file, because
    ``rmtree`` on one raises something less legible three frames down.
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
    """Replace ``root`` with ``snap``, removing anything the snapshot did not have.

    Refuses unless both trees are identifiable corpora and neither contains the other, then
    **copies before it deletes**: the replacement is staged beside ``root``, ``root`` is moved
    aside, the staged copy takes its place, and only then is the old tree removed. An
    interruption anywhere in that sequence leaves ``root`` or ``root.replaced`` intact, where the
    old delete-then-copy order left a window with the corpus in neither place.
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
    """Whether ``root``'s content hash has moved away from ``expected``."""
    return corpus_content_hash(root) != expected
