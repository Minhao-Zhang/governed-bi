"""``corpus_content_hash`` is taken over bytes, so every writer has to write the same bytes.

``corpus/hash.py`` digests ``path.read_bytes()``. That is the right choice — a digest over
parsed content would not notice a file the loader silently repairs — and it makes every
``write_text`` in the tree part of the hash's definition. ``Path.write_text`` with no
``newline=`` translates ``\\n`` to ``os.linesep``, so until 2026-09-18 an asset written on
Windows and the identical asset written on Linux produced two different digests.

**The checkout was already defended and the write side was not.**
``../BIRD-corpus/.gitattributes`` is ``* -text`` with a nine-line header about exactly this
hazard: "Git's end-of-line conversion is on by default on Windows … a fresh clone would
compute a *different* corpus hash from the tree that produced the number". That stops git
from rewriting bytes on checkout. It cannot stop this engine from rewriting them on write,
and ``corpus/store.py::write`` did.

What it reaches: every arm digest in ``register/arms.toml`` is keyed on
``corpus_content_hash``, and ``measure/gates.py``'s corpus gate compares rows on it. A
Windows-side corpus edit moved the hash for reasons that had nothing to do with the corpus.

The assertion is on **bytes**, not on a round-trip: PyYAML normalises ``\\r\\n`` inside quoted
scalars, so a load-then-compare passes on a CRLF file and says nothing. The damage is
bytes-only, which is precisely what this project pins identity to.
"""

from __future__ import annotations

import pathlib
import tempfile

from governed_bi.corpus.hash import corpus_content_hash
from governed_bi.corpus.schema import TableAsset
from governed_bi.corpus.store import write


def _asset(name: str = "t") -> TableAsset:
    return TableAsset(id=f"s.{name}", schema="s", physical_name=name, summary="a table")


def test_a_written_asset_carries_no_carriage_return() -> None:
    """The property, stated where a reader on any OS can check it.

    Asserted as "no CR" rather than "os.linesep is LF": the test has to fail on Windows,
    where the defect lived, and pass on the Linux CI that never saw it.
    """
    root = pathlib.Path(tempfile.mkdtemp(prefix="corpus-eol-"))
    path = write(root, _asset())

    raw = path.read_bytes()
    assert b"\r" not in raw, (
        "the corpus was written with platform line endings, so this tree hashes differently "
        "on Windows than on Linux and every arm digest keyed on corpus_content_hash moves"
    )
    assert raw.count(b"\n") > 1, "nothing was written, so the assertion above proved nothing"


def test_the_same_asset_hashes_the_same_whatever_wrote_the_bytes() -> None:
    """The consequence, end to end through the digest the arms are keyed on.

    The control is a hand-written CRLF copy of the *same* asset: without it this asserts only
    that a hash function is deterministic, which it would be either way.
    """
    engine_root = pathlib.Path(tempfile.mkdtemp(prefix="corpus-engine-"))
    engine_path = write(engine_root, _asset())

    crlf_root = pathlib.Path(tempfile.mkdtemp(prefix="corpus-crlf-"))
    crlf_path = crlf_root / engine_path.relative_to(engine_root)
    crlf_path.parent.mkdir(parents=True, exist_ok=True)
    crlf_path.write_bytes(engine_path.read_bytes().replace(b"\n", b"\r\n"))

    assert corpus_content_hash(engine_root) != corpus_content_hash(crlf_root), (
        "the digest is insensitive to line endings, so this file is asserting nothing — "
        "check `corpus/hash.py` still reads bytes"
    )

    second_root = pathlib.Path(tempfile.mkdtemp(prefix="corpus-second-"))
    write(second_root, _asset())
    assert corpus_content_hash(engine_root) == corpus_content_hash(second_root), (
        "two runs of the engine's own writer disagree, which is the defect this covers"
    )
