"""``corpus_content_hash`` — treatment identity (one implementation, no sentinel).

Raises on a missing root. Sensitive to content, stable, relative sorted paths.
Unreadable files are named in the digest without their bytes.
"""


from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping, Sequence

from .identity import corpus_files

__all__ = ["corpus_content_hash"]

#: Placeholder for a file that exists but cannot be read (not an unknown digest).
_UNREADABLE = b"<unreadable>"


def corpus_content_hash(
    root: Path | str,
    *,
    schemas: Sequence[str] | None = None,
    overrides: Mapping[Path, bytes] | None = None,
) -> str:
    """A hex digest over the corpus stored under ``root``.

    ``schemas`` restricts the digest to those subtrees, so an arm's treatment identity
    covers exactly the schemas it served and a leftover subtree enters neither.

    Raises ``FileNotFoundError`` on a missing ``root`` rather than returning a sentinel:
    two absences would compare equal and pass a comparability gate.

    Hashes **every** file in the selected subtrees, not just ``.yaml`` — the markdown D9
    keeps beside the assets is corpus content, and ignoring it reports two different
    corpora as the same treatment.

    ``overrides`` substitutes bytes for a file already in the walk, giving **the digest the tree
    would have after those writes**. That is what makes ``DerivedState.landed_verified`` mean
    something: the exporter can record the hash of the tree its bundle predicts, so a later landing
    can be told apart from a landing that arrived with three other bundles. Without it the strongest
    available answer is ``landed_matched``, and the field the lifecycle reads was never set by
    anything but a test.

    A parameter here rather than a second function, because two digest implementations is how two
    answers to "is this the same corpus" come to disagree — the same reason this module's own
    docstring says "one implementation, no sentinel".

    An override naming a path outside ``root`` **raises**. It cannot be in the walk, so honouring it
    would describe a tree that cannot exist and ignoring it would return the *unedited* digest —
    which reports ``superseded`` on a landing that went perfectly.
    """
    base = Path(root)
    if not base.is_dir():
        raise FileNotFoundError(
            f"no corpus at {base}. There is no digest for a corpus that is not there, "
            "and returning one would be v1's 'unknown' sentinel: a value that compares "
            "equal to another run's missing corpus and passes the comparability gate"
        )

    replaced: dict[Path, bytes] = {}
    for path, payload in (overrides or {}).items():
        resolved = Path(path).resolve()
        try:
            resolved.relative_to(base.resolve())
        except ValueError as err:
            raise ValueError(
                f"override {path} is not under the corpus root {base}. A digest of a tree "
                "that cannot exist is worse than no digest, and ignoring it would return the "
                "unedited hash -- which reads as `superseded` on a landing that went "
                "perfectly."
            ) from err
        replaced[resolved] = payload

    digest = hashlib.sha256()
    for path in corpus_files(base, schemas=schemas):
        digest.update(path.relative_to(base).as_posix().encode("utf-8"))
        digest.update(b"\0")
        override = replaced.get(path.resolve())
        if override is not None:
            payload = override
        else:
            try:
                payload = path.read_bytes()
            except OSError:
                payload = _UNREADABLE
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()
