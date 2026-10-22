"""``corpus_content_hash`` reads bytes, so writers must emit the same bytes on every OS.

``write`` forces LF. A load-and-compare would hide a carriage return inside a YAML scalar.
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
    """Assert no carriage return, so the check fails on a CRLF host."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="corpus-eol-"))
    path = write(root, _asset())

    raw = path.read_bytes()
    assert b"\r" not in raw, (
        "the corpus was written with platform line endings, so this tree hashes differently "
        "on Windows than on Linux and every config digest keyed on corpus_content_hash moves"
    )
    assert raw.count(b"\n") > 1, "nothing was written, so the assertion above proved nothing"


def test_the_same_asset_hashes_the_same_whatever_wrote_the_bytes() -> None:
    """A CRLF copy of the same asset must hash differently, or this only checks determinism."""
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


def test_the_writer_does_not_rely_on_the_platform_newline(monkeypatch) -> None:
    """Force a CRLF default, including where the platform newline is LF.

    The shim rewrites only a ``write_text`` call that omits ``newline``. The check is on bytes.
    """
    real_write_text = pathlib.Path.write_text

    def as_if_windows(self, data, encoding=None, errors=None, newline=None):
        if newline is None:
            data = data.replace("\n", "\r\n")
        return real_write_text(self, data, encoding=encoding, errors=errors, newline="\n")

    monkeypatch.setattr(pathlib.Path, "write_text", as_if_windows)

    root = pathlib.Path(tempfile.mkdtemp(prefix="corpus-asif-"))
    raw = write(root, _asset()).read_bytes()

    assert raw.count(b"\n") > 1, "nothing was written, so the assertion below proves nothing"
    assert b"\r" not in raw, (
        "corpus/store.py::write let the platform choose the line ending. On a CRLF host the "
        "same corpus then hashes differently, and every config digest in registry/configs.toml is "
        "keyed on corpus_content_hash"
    )
