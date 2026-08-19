"""``SqliteConnector`` must survive being called from a different thread than the one that
opened it.

**Why this matters, and why it is not a theoretical case.** LangGraph's node executor runs
each node — including ``run_query``'s tool call — in its own worker thread (``ThreadPoolExecutor``
under ``wrap_node``'s ``asyncio.to_thread``). ``_connect()`` memoized ``self._conn`` per
*instance*, not per *thread*, so the first call's thread owns the handle and every later call
from a different thread hit Python's own guard: ``sqlite3.ProgrammingError: SQLite objects
created in a thread can only be used in that same thread.`` Reproduced live against the real
serve graph on both OpenAI and Bedrock (RESUME.md, 2026-08-06) — not a hypothetical, the actual
cause of two independent 15+-minute hangs before the agent's retry loop (a separate, since-fixed
defect, upstream `c8b570d`) turned the crash into an infinite loop instead of a clean error.

The fix is to stop caching across calls: a read-only local file connection is cheap enough to
open per statement, and "cheap to open, expensive to share across threads" is exactly the
tradeoff a per-call connection is for.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from governed_bi.datasource.sqlite import SqliteConnector


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "thread_safety.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.execute("INSERT INTO t VALUES (1), (2), (3)")
    conn.commit()
    conn.close()
    return path


def test_execute_survives_a_call_from_a_different_thread_than_the_one_that_opened_it(
    db_path,
) -> None:
    connector = SqliteConnector(db_path)

    # First call, on this (the main) thread — this is what used to poison every later call
    # from anywhere else, because `_connect()` cached the handle here.
    columns, rows, _ = connector.execute("SELECT COUNT(*) FROM t")
    assert rows == [(3,)]

    # Second call, from a genuinely different thread — the exact shape of a LangGraph tool
    # node running after the connector's first use. Must not raise.
    result: dict[str, object] = {}

    def call_from_worker_thread() -> None:
        try:
            result["rows"] = connector.execute("SELECT COUNT(*) FROM t")[1]
        except Exception as exc:  # noqa: BLE001 - captured for the assertion below, not swallowed
            result["error"] = exc

    worker = threading.Thread(target=call_from_worker_thread)
    worker.start()
    worker.join(timeout=5)

    assert "error" not in result, (
        f"a call from a different thread raised: {result.get('error')!r} — this is the "
        "cross-thread connection-reuse defect, not a fresh, unrelated failure"
    )
    assert result.get("rows") == [(3,)]


def test_introspect_survives_a_call_from_a_different_thread_than_the_one_that_opened_it(
    db_path,
) -> None:
    """Same defect class, the other public entry point that calls ``_connect()``."""
    connector = SqliteConnector(db_path)
    connector.introspect()

    result: dict[str, object] = {}

    def call_from_worker_thread() -> None:
        try:
            result["tables"] = [t.physical_name for t in connector.introspect().tables]
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    worker = threading.Thread(target=call_from_worker_thread)
    worker.start()
    worker.join(timeout=5)

    assert "error" not in result, f"a call from a different thread raised: {result.get('error')!r}"
    assert result.get("tables") == ["t"]


def test_many_sequential_calls_from_alternating_threads_all_succeed(db_path) -> None:
    """Not just "the second caller" — every caller, in any order, on any thread.

    A fix that special-cased "the first call after construction" would still break on a third
    thread; alternating threads across several calls is what actually exercises "no caching
    across threads at all," rather than "caching survives exactly one handoff."
    """
    connector = SqliteConnector(db_path)
    errors: list[Exception] = []

    def call() -> None:
        try:
            connector.execute("SELECT COUNT(*) FROM t")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    for _ in range(6):
        t = threading.Thread(target=call)
        t.start()
        t.join(timeout=5)

    assert not errors, f"{len(errors)} of 6 alternating-thread calls raised: {errors!r}"
