"""Suite-wide setup. Its only job is to load ``.env`` once, at process entry.

**Why here and not in the fixtures.** A library must not read ``.env`` — `src/` takes a DSN
or a client and does not decide its own configuration behind its caller's back, and
`tools/check_imports.py` keeps that layering. So *something* has to bridge the developer's
``.env`` to the process environment, and the honest place is the entry point of the process
that wants it. For the test suite, that is this file.

The alternative is what the suite actually had on 2026-08-03, and it is worth recording
because the cost was invisible: three separate readers of "environment or ``.env``", one of
which read only `os.environ`. Parcel I's contract therefore **skipped its OpenAI half over a
key that was in ``.env`` the whole time**, and reported in capital letters that it had
exercised one adapter of two — a true sentence about a false situation. Then, when the reader
was unified, the adapter itself still raised, because `OpenAIEmbedder` reads `os.environ` and
is right to. Bridging per-fixture cannot fix that; bridging at entry can.

**Existing environment always wins.** A value already in the environment is an explicit
override for this run — `GOVERNED_BI_PG_DSN=... pytest` must point at another database
without editing a file — so this never overwrites one. It only fills gaps.

**No value is ever printed.** The count of names filled is reported, not the names, and
certainly not the values: a conftest that echoes what it loaded puts credentials in every
CI log that runs the suite.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


def _load_dotenv_into_environ() -> int:
    """Fill unset environment variables from ``.env``. Returns how many were filled."""
    from governed_bi.credentials import _dotenv  # noqa: PLC2701 -- one reader, shared

    filled = 0
    for key, value in _dotenv().items():
        if value and not os.environ.get(key):
            os.environ[key] = value
            filled += 1
    return filled


#: Run at import, which pytest does before collecting any test — so a module-scoped fixture
#: that reads a credential at collection time already sees it.
_FILLED = _load_dotenv_into_environ()


@pytest.fixture(autouse=True)
def _isolate_runtime_overrides(tmp_path_factory, monkeypatch):
    """No test reads the operator's real switches.

    ``serve/runtime_overrides.py`` persists to ``runs/runtime-overrides.json``, and every session
    layers it on. Without this, a switch someone flipped in a browser changes what the suite
    asserts — which is exactly how it was found: ``test_capabilities_reports_the_ported_detentai
    _toggles`` failed on a machine where ``enable_clarification_to_draft`` had just been turned on
    from the UI. Autouse and repository-wide rather than per-file, because the failure mode is a
    test that never thought about the file at all.

    Same precaution ``api/trace_store.py``'s ``TURN_LOG_DIR`` takes, for the same reason.
    """
    from governed_bi.serve import runtime_overrides

    monkeypatch.setattr(
        runtime_overrides,
        "OVERRIDE_PATH",
        tmp_path_factory.mktemp("overrides") / "runtime-overrides.json",
    )
    runtime_overrides.reload()
    yield
    runtime_overrides.reload()
