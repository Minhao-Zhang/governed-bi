"""Suite-wide test hygiene.

The test suite is **hermetic and offline by design**: it must never reach a live
model (live-model behavior is exercised only by ``scripts/live_smoke.py``, a
manual entrypoint). But ``import governed_bi`` auto-loads a repo-root ``.env`` as
a local-run convenience (see :func:`governed_bi.config.load_dotenv`), so a
developer who keeps their real ``OPENAI_API_KEY`` in ``.env`` would otherwise
leak it into the test process. That flips "offline" code paths (e.g. the viz chat
view, which selects the template generator only when no key is present) onto the
live model - non-deterministic, order-dependent, and real API spend.

So we strip ``OPENAI_API_KEY`` for the whole session. Tests that need a key set
their own via ``monkeypatch.setenv`` (restored per test). This fixture runs after
collection-time imports (where the ``.env`` autoload happens), unlike
``pytest_configure``, so popping here actually sticks.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _hermetic_offline_env():
    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["OPENAI_API_KEY"] = saved
