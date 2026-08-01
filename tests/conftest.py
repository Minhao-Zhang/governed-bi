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

# Secrets/toggles that must not leak from a developer's .env into the hermetic
# suite: the model key (would flip offline paths onto the live model) and the
# external-tracing switches (would make tests phone home to Langfuse/LangSmith
# now that the agent path threads tracing_callbacks() into its run config).
_STRIPPED_ENV = (
    "OPENAI_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGSMITH_API_KEY",
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
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


@pytest.fixture(autouse=True)
def _fresh_default_stack():
    """Drop the process-wide default ServeStack around every test (N14).

    ``api.stack.get_default_stack()`` memoises one stack per process so
    ``api.routes`` and ``api.graph_app`` cannot each open a second corpus /
    checkpointer / index cache. That cache is correct for a server process and
    hostile to a test suite: whichever test happens to run first decides what
    every later ``create_app()`` / ``make_graph()`` sees, under whatever env
    vars, settings, or tmp corpus that first test had. Worse, the cached stack
    is shared *mutable* state despite being a frozen dataclass — importing
    ``governed_bi.api.routes`` does ``object.__setattr__(_stack, "can_stream",
    True)`` on it, which used to leak into
    ``test_capabilities_reports_offline_dev`` and fail it whenever the shuffled
    order put ``test_routes_app_advertises_streaming`` first.

    So reset before and after each test: the singleton stays (it is the point of
    N14) but its lifetime is scoped to one test, and test order stops being
    load-bearing. Rebuilding is cheap — a corpus load plus a SQLite probe.
    """
    from governed_bi.api.stack import _reset_default_stack_for_tests

    _reset_default_stack_for_tests()
    try:
        yield
    finally:
        _reset_default_stack_for_tests()


@pytest.fixture(autouse=True)
def _fresh_embedding_memos():
    """Drop the two process-wide embedding memos around every test.

    ``schema_router._SCHEMA_VECTOR_MEMO`` and ``rvgd._ASSET_VECTOR_MEMO`` exist so
    the eval harness's worker threads pay for one embed of a given text instead of
    N. They are keyed on content, so within a run they cannot serve a stale vector
    — but across a *test suite* they make order load-bearing in exactly the way
    ``_fresh_default_stack`` describes: several tests build the same three-table
    fixture with the same fake embedder and then assert on the number of embed
    calls, and whichever ran second would see zero.
    """
    from governed_bi.retrieval.rvgd import _reset_asset_vector_memo_for_tests
    from governed_bi.retrieval.schema_router import (
        _reset_schema_vector_memo_for_tests,
    )

    def _clear() -> None:
        _reset_asset_vector_memo_for_tests()
        _reset_schema_vector_memo_for_tests()

    _clear()
    try:
        yield
    finally:
        _clear()
