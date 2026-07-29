"""One durable file replace, because there were three copies and each had half of it.

Every artifact a run is quoted from — ``summary.json``, ``manifest.json``,
``generations.<arm>.jsonl``, ``runs/index.jsonl`` — is written by overwriting a whole
file. A kill or a power loss mid-write does not damage a tail there, it truncates the
record, so all four went through a temp-file-then-``os.replace`` dance. Three separate
copies of that dance grew, and no copy had both halves of it:

- ``metrics.write_manifest`` and ``harness._write_jsonl`` flushed and ``fsync``-ed, so
  the bytes were on the platter before the swap — but called a bare ``os.replace``.
- ``index.append_run`` retried the swap, because on Windows ``os.replace`` over a file
  any process holds **open for reading** raises ``PermissionError: [WinError 5]``:
  the ledger open in an editor, or a virus scanner, or the reader the runbook itself
  tells the operator to run, was enough to lose the record (reproduced at 8 writers x
  40 appends with one concurrent reader: 8 of 320 records survived). But it wrote via
  ``Path.write_text``, which never syncs.

So the two run artifacts were exposed to exactly the failure the ledger had already
been fixed for, and the ledger was exposed to the one the artifacts had. Both bugs
were invisible because each copy looked careful on its own. Hence one function.

Newline translation is deliberately left to the platform (no ``newline=`` argument),
matching what all three copies did: passing ``newline="\\n"`` here would silently
change the bytes of every artifact this repo has already written on Windows.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

#: How long to keep retrying a blocked swap before giving up. The caller's data is
#: already durable on disk at that point (that is the whole ordering below), so a
#: raise here loses the swap, never the bytes.
REPLACE_TIMEOUT_S = 30.0


def replace_with_retry(tmp: Path, dest: Path, *, timeout_s: float = REPLACE_TIMEOUT_S) -> None:
    """``os.replace(tmp, dest)``, retried while a *reader* is blocking it on Windows.

    Separate from :func:`atomic_write_text` because the ledger renders its whole text
    under a lock it already holds and swaps in a second step.

    The run's own ``summary.json`` and ``manifest.json`` are on disk by the time the
    ledger swap runs, so the data survives a failure here and
    ``python -m governed_bi.eval.index --add <run_dir>`` re-indexes it. That is a
    documented recovery, not a reason to let a multi-hour run end on a traceback.
    """
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                os.replace(tmp, dest)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
    finally:
        # A failed swap used to leave the ``.tmp<pid>`` beside the ledger, one per
        # failure, where ``load_index`` does not read it and nobody collects it.
        tmp.unlink(missing_ok=True)


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    timeout_s: float = REPLACE_TIMEOUT_S,
) -> None:
    """Overwrite ``path`` with ``text``: temp file, flush, ``fsync``, retried replace.

    The order is the point. ``fsync`` before the swap is what makes the file either
    wholly old or wholly new after a crash; syncing after, or not at all, leaves a
    window where the directory entry points at unwritten blocks.

    Creates ``path.parent`` — a caller writing the first artifact of a fresh run
    directory should not have to know whether some earlier step made it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    with tmp.open("w", encoding=encoding) as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    replace_with_retry(tmp, path, timeout_s=timeout_s)
