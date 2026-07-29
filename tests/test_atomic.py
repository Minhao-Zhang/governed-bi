"""The shared durable writer, and the two halves its three predecessors each missed.

Every artifact a run is quoted from is written by overwriting a whole file, so a kill
mid-write truncates the record rather than damaging a tail. Three copies of the
temp-then-replace dance grew independently and no copy had both halves: the manifest
and generations writers synced but swapped with a bare ``os.replace``, while the
ledger retried the swap but wrote via ``Path.write_text``, which never syncs. Each
looked careful on its own, which is why neither gap was noticed. These tests pin both.
"""

from __future__ import annotations

import os

import pytest

from governed_bi.eval.atomic import atomic_write_text, replace_with_retry


def test_it_writes_the_text_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "summary.json"
    atomic_write_text(path, '{"ex": 0.5}\n')
    assert path.read_text(encoding="utf-8") == '{"ex": 0.5}\n'
    # A leaked ``.tmp<pid>`` beside a run artifact is not read by anything and nobody
    # collects it — the ledger accumulated one per failure before this was fixed.
    assert [p.name for p in tmp_path.iterdir()] == ["summary.json"]


def test_it_creates_the_parent_directory(tmp_path):
    """A caller writing the FIRST artifact of a fresh run directory should not have to
    know whether some earlier step already made it. ``write_split_gap`` hit exactly
    this: it wrote the gap report into a run root no split had created yet."""
    path = tmp_path / "runs" / "20260729T000000Z" / "split_gap.json"
    atomic_write_text(path, "{}\n")
    assert path.exists()


def test_the_bytes_are_synced_before_the_swap(tmp_path, monkeypatch):
    """The ORDER is the whole point. ``fsync`` before the replace is what makes the
    file wholly old or wholly new after a crash; syncing after — or not at all, which
    is what the ledger did — leaves a window where the directory entry points at
    unwritten blocks. Recorded as a sequence because either call alone proves nothing.
    """
    from governed_bi.eval import atomic as atomic_mod

    seen: list[str] = []
    real_fsync, real_replace = atomic_mod.os.fsync, atomic_mod.os.replace

    def spy_fsync(fd):
        seen.append("fsync")
        return real_fsync(fd)

    def spy_replace(src, dst, *a, **kw):
        seen.append("replace")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(atomic_mod.os, "fsync", spy_fsync)
    monkeypatch.setattr(atomic_mod.os, "replace", spy_replace)
    atomic_write_text(tmp_path / "manifest.json", "{}\n")

    assert seen == ["fsync", "replace"], (
        "the payload must be durable BEFORE the swap makes it visible"
    )


def test_a_reader_blocking_the_swap_is_retried_not_lost(tmp_path, monkeypatch):
    """On Windows ``os.replace`` over a file any process holds open for READING raises
    ``PermissionError: [WinError 5]``. The lock serialises writers and does nothing
    about readers, so an editor, a virus scanner, or the reader the runbook itself
    tells the operator to run was enough to lose the record. The manifest and
    generations writers were exposed to this until they moved onto this function."""
    from governed_bi.eval import atomic as atomic_mod

    real_replace = atomic_mod.os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "Access is denied")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(atomic_mod.os, "replace", flaky_replace)
    atomic_write_text(tmp_path / "manifest.json", "{}\n")

    assert calls["n"] >= 3, "the swap was not contended, so the retry path never ran"
    assert (tmp_path / "manifest.json").read_text(encoding="utf-8") == "{}\n"


def test_a_swap_that_never_succeeds_raises_and_still_cleans_up(tmp_path, monkeypatch):
    """Retrying forever would hang a finished run. The caller's bytes are already
    durable at this point, so raising loses the swap and never the data — and the temp
    file must not survive to accumulate."""
    from governed_bi.eval import atomic as atomic_mod

    def always_blocked(src, dst, *a, **kw):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(atomic_mod.os, "replace", always_blocked)
    with pytest.raises(PermissionError):
        atomic_write_text(tmp_path / "manifest.json", "{}\n", timeout_s=0.05)

    assert not list(tmp_path.iterdir()), "a failed swap left its temp file behind"


def test_the_swap_helper_is_usable_on_its_own(tmp_path):
    """The ledger renders its whole text under a lock it already holds and swaps in a
    second step, so the replace half has to stand alone."""
    src, dest = tmp_path / "a.tmp", tmp_path / "a.jsonl"
    src.write_text("row\n", encoding="utf-8")
    replace_with_retry(src, dest)
    assert dest.read_text(encoding="utf-8") == "row\n"
    assert not src.exists()


def test_platform_newline_translation_is_left_alone(tmp_path):
    """Deliberately no ``newline=`` argument, matching all three predecessors: forcing
    ``\\n`` would silently change the bytes of every artifact this repo has already
    written on Windows, including runs whose numbers are in the ledger."""
    path = tmp_path / "x.jsonl"
    atomic_write_text(path, "a\nb\n")
    expected = ("a\nb\n").replace("\n", os.linesep).encode()
    assert path.read_bytes() == expected
