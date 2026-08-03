"""``corpus_content_hash`` -- the treatment identity. One implementation, no sentinel.

The corpus **is** the treatment (``register/record.py``'s ``corpus_content_hash``
row). ADR 0005's delivery gate reads this digest to prove that two arms received
different corpora, so every property below is a property of that gate:

* **No in-band value meaning "I do not know."** v1's ``corpus_content_hash ==
  "unknown"`` compared equal to itself, so two runs **with no recorded treatment at
  all** passed the comparability gate. Absence is expressed by not having a digest:
  this function raises for a missing root rather than returning a string that
  happens to be equal to another run's missing root.
* **Sensitive to content**, or the gate passes two byte-identical arms.
* **Stable across calls**, or every comparison is incomparable -- which reads as
  "the treatment differed" and passes the same gate for the wrong reason. Both
  directions are asserted in the acceptance contract, because a hash satisfying one
  and not the other is a plausible implementation.
* **Paths relative and sorted**, so a staging directory cannot leak into the digest
  and two checkouts of one corpus agree.
* **A file that exists and cannot be read is named in the digest without its
  bytes.** Skipping it silently made an unreadable corpus hash identically to one
  that was never written.

``tools/check_one_implementation.py`` declares this module as the concept's only
home. Two hash implementations would reproduce v1's defect from the other side: two
runs with the same corpus and different digests.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Sequence

from .identity import corpus_files

__all__ = ["corpus_content_hash"]

#: Recorded in place of a file's bytes when the bytes cannot be read.
#:
#: This is **not** an "unknown" sentinel for the digest. It names one file inside a
#: digest that still exists and still differs from every other tree -- the opposite
#: of a whole-corpus value that compares equal to itself. An unreadable file changes
#: the hash, which is the behaviour that was missing.
_UNREADABLE = b"<unreadable>"


def corpus_content_hash(root: Path | str, *, schemas: Sequence[str] | None = None) -> str:
    """A hex digest over the corpus stored under ``root``.

    ``schemas`` restricts the digest to those subtrees, so an arm's treatment
    identity covers exactly the schemas that arm served -- a leftover subtree from
    another attempt neither enters the load nor the digest.

    Raises ``FileNotFoundError`` when ``root`` does not exist. That is the whole
    argument of this module: the absence of a corpus is reported out of band, never
    as a digest value, because a value can be compared and two absences would
    compare equal.

    Hashes **every** file in the selected subtrees, not just ``.yaml``. The markdown
    D9 keeps beside the assets is corpus content too, and a digest that ignored it
    would report two different corpora as the same treatment.
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
