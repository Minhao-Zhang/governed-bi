"""``corpus_content_hash`` — treatment identity (one implementation, no sentinel).

Raises on a missing root. Sensitive to content, stable, relative sorted paths.
Unreadable files are named in the digest without their bytes.
"""


from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Sequence

from .identity import corpus_files

__all__ = ["corpus_content_hash"]

#: Placeholder for a file that exists but cannot be read (not an unknown digest).
_UNREADABLE = b"<unreadable>"


def corpus_content_hash(root: Path | str, *, schemas: Sequence[str] | None = None) -> str:
    """A hex digest over the corpus stored under ``root``.

    ``schemas`` restricts the digest to those subtrees, so an arm's treatment identity
    covers exactly the schemas it served and a leftover subtree enters neither.

    Raises ``FileNotFoundError`` on a missing ``root`` rather than returning a sentinel:
    two absences would compare equal and pass a comparability gate.

    Hashes **every** file in the selected subtrees, not just ``.yaml`` — the markdown D9
    keeps beside the assets is corpus content, and ignoring it reports two different
    corpora as the same treatment.
    """
    base = Path(root)
    if not base.is_dir():
        raise FileNotFoundError(
            f"no corpus at {base}. There is no digest for a corpus that is not there, "
            "and returning one would be v1's 'unknown' sentinel: a value that compares "
            "equal to another run's missing corpus and passes the comparability gate"
        )

    digest = hashlib.sha256()
    for path in corpus_files(base, schemas=schemas):
        digest.update(path.relative_to(base).as_posix().encode("utf-8"))
        digest.update(b"\0")
        try:
            payload = path.read_bytes()
        except OSError:
            payload = _UNREADABLE
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()
