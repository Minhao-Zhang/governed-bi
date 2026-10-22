"""``sqlite3`` connections are thread-affine, and LangGraph runs ``run_query`` on a worker thread.

Two live hangs of more than 15 minutes were this cache being reused across threads.
A read-only file is cheap to open per call; a lock serialises every call, and
``check_same_thread=False`` is unsafe.
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

    columns, rows, _ = connector.execute("SELECT COUNT(*) FROM t")
    assert rows == [(3,)]

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
    """``introspect`` is the other public call that opens a connection."""
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
    """Several threads, not a single handoff after construction.
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
