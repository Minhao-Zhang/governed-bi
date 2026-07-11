"""Suite-wide test hygiene.

The test suite is **hermetic and offline by design**: it must never reach a live
model (live-model behavior is exercised only by ``scripts/live_smoke.py``, a
manual entrypoint). But ``import governed_bi`` auto-loads a repo-root ``.env`` as
a local-run convenience (see :func:`governed_bi.config.load_dotenv`), so a
developer who keeps their real ``OPENAI_API_KEY`` in ``.env`` would otherwise
leak it into the test process. That flips "offline" code paths (e.g. the viz chat
view, which selects the template generator only when no key is present) onto the
live model - non-deterministic, order-dependent, and real API spend.

So we strip ``OPENAI_API_KEY`` for the whole session. We also strip the
``GOVERNED_BI_*`` corpus / data-source overrides, so a developer whose ``.env``
points the running server at a sibling corpus repo or a Postgres instance does
not redirect the tests off the committed SQLite fixture. Tests that need any of
these set their own via ``monkeypatch.setenv`` (restored per test). This fixture
runs after collection-time imports (where the ``.env`` autoload happens), unlike
``pytest_configure``, so popping here actually sticks.
"""

from __future__ import annotations

import os

import pytest

# Local-run overrides that must not leak from a developer's .env into the
# hermetic suite (which must use the committed corpus/ fixture + SQLite).
_STRIPPED_ENV = (
    "OPENAI_API_KEY",
    "GOVERNED_BI_CORPUS",
    "GOVERNED_BI_DB",
    "GOVERNED_BI_DB_KIND",
    "GOVERNED_BI_DB_SCHEMA",
    "GOVERNED_BI_DB_DSN",
    "GOVERNED_BI_DB_DSN_ENV",
    "GOVERNED_BI_SQLITE",
)


@pytest.fixture(scope="session", autouse=True)
def _hermetic_offline_env():
    saved = {k: os.environ.pop(k, None) for k in _STRIPPED_ENV}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
