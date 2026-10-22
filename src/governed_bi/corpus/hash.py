"""``corpus_content_hash``: one implementation, no sentinel.

A missing root raises. An unreadable file contributes a fixed placeholder, not its bytes.
"""


from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping, Sequence

from .identity import corpus_files

__all__ = ["corpus_content_hash"]

#: Bytes mixed in for an unreadable file. Not a sentinel digest.
_UNREADABLE = b"<unreadable>"


def corpus_content_hash(
    root: Path | str,
    *,
    schemas: Sequence[str] | None = None,
    overrides: Mapping[Path, bytes] | None = None,
) -> str:
    """One digest of the tree under ``root``. No sentinel.

    A missing root raises, so two absences cannot compare equal.
    ``schemas`` limits the walk. An override outside ``root`` raises; a second function would disagree about sameness.
    """
    base = Path(root)
    if not base.is_dir():
        raise FileNotFoundError(
            f"no corpus at {base}. There is no digest for a corpus that is not there, "
            "and returning one would be v1's 'unknown' sentinel: a value that compares "
            "equal to another run's missing corpus and passes the comparability check"
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
