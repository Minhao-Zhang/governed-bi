"""Suite-wide test hygiene.

The test suite is **hermetic and offline by design**: it must never reach a live
model (live-model behavior is exercised only by ``scripts/live_smoke.py``, a
manual entrypoint). But ``import governed_bi`` auto-loads a repo-root ``.env`` as
a local-run convenience (see :func:`governed_bi.config.load_dotenv`), so a
developer who keeps their real ``OPENAI_API_KEY`` in ``.env`` would otherwise
leak it into the test process. That flips "offline" code paths onto the live
model - non-deterministic, order-dependent, and real API spend.

So we strip ``OPENAI_API_KEY`` for the whole session. We also disable
``governed_bi.local.toml`` merging so a developer's local Postgres/corpus
overlay cannot redirect the suite off the committed SQLite fixture. Tests that
need a custom Settings pass one explicitly to ``build_stack`` / ``load_settings``.
"""

from __future__ import annotations

import os

import pytest

import governed_bi.config as _config

# Disable local TOML overlay for the hermetic suite (module flag, not an env var).
_config.APPLY_LOCAL_OVERLAY = False

# Secret that must not leak from a developer's .env into the hermetic suite.
_STRIPPED_ENV = ("OPENAI_API_KEY",)


@pytest.fixture(scope="session", autouse=True)
def _hermetic_offline_env():
    saved = {k: os.environ.pop(k, None) for k in _STRIPPED_ENV}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
