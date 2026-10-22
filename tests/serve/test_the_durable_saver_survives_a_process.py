"""The durable checkpointer, opened for real.

``aiosqlite`` hangs when a saver is reused across two ``asyncio.run`` calls, an unclosed graph
holds a non-daemon thread, and sync ``delete_thread`` blocks. Eviction uses ``adelete_thread``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from governed_bi.serve.graph import compile_durable


def _config(thread: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}


def _write_one(graph: Any, thread: str, values: dict[str, Any]) -> None:
    """Write a checkpoint through the saver, without running a turn."""
    graph.run_coro(graph.checkpointer.aput(
        _config(thread),
        {
            "v": 4,
            "id": f"cp-{thread}",
            "ts": "2026-08-18T00:00:00+00:00",
            "channel_values": dict(values),
            "channel_versions": {k: "1" for k in values},
            "versions_seen": {},
        },
        {"source": "update", "step": 1, "parents": {}},
        {},
    ))


def test_a_checkpoint_written_by_one_saver_is_read_by_the_next(tmp_path: Path) -> None:
    """Two savers on one file, in two event loops, which is what a restart is."""
    db = tmp_path / "conversations.sqlite"

    first = compile_durable(path=db)
    try:
        assert isinstance(first.checkpointer, BaseCheckpointSaver)
        _write_one(first, "t-durable", {"question": "how many buildings?"})
    finally:
        first.close()

    second = compile_durable(path=db)
    try:
        tuple_ = second.run_coro(second.checkpointer.aget_tuple(_config("t-durable")))
    finally:
        second.close()

    assert tuple_ is not None, (
        "the checkpoint did not survive the saver that wrote it, so the store is not durable and "
        "`/capabilities`' `checkpoint_durable` is a false claim"
    )
    assert tuple_.checkpoint["channel_values"]["question"] == "how many buildings?"


def test_the_saver_answers_more_than_once_rather_than_hanging(tmp_path: Path) -> None:
    """A second read on the same saver must return; a closed loop hangs instead of raising."""
    graph = compile_durable(path=tmp_path / "twice.sqlite")
    try:
        _write_one(graph, "t-twice", {"question": "first"})
        assert graph.run_coro(graph.checkpointer.aget_tuple(_config("t-twice"))) is not None
        _write_one(graph, "t-twice", {"question": "second"})
        again = graph.run_coro(graph.checkpointer.aget_tuple(_config("t-twice")))
    finally:
        graph.close()
    assert again is not None
    assert again.checkpoint["channel_values"]["question"] == "second"


def test_closing_releases_the_connection_so_the_file_is_usable(tmp_path: Path) -> None:
    """After ``close`` a plain sqlite connection can read the file, so the saver released it."""
    db = tmp_path / "closed.sqlite"
    graph = compile_durable(path=db)
    _write_one(graph, "t-closed", {"question": "q"})
    graph.close()

    assert not db.with_name(db.name + "-wal").exists(), (
        "a write-ahead log outlived the saver, so the connection did not close and this process "
        "is holding a non-daemon thread that will stop it from exiting"
    )
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("select count(*) from checkpoints").fetchone()[0]
    finally:
        conn.close()
    assert rows >= 1


def test_closing_twice_is_harmless_and_a_default_graph_has_nothing_to_close() -> None:
    """``close`` on the default graph is a no-op, so a second close does not tear the saver down."""
    from governed_bi.serve.graph import compile_graph

    plain = compile_graph()
    plain.close()
    plain.close()
    assert plain.invoke is not None  # still usable: nothing was torn down


def test_a_thread_can_be_evicted_through_the_async_method(tmp_path: Path) -> None:
    """Eviction goes through ``adelete_thread``; the sync method blocks when its loop is idle."""
    graph = compile_durable(path=tmp_path / "evict.sqlite")
    try:
        _write_one(graph, "t-evict", {"question": "q"})
        assert graph.run_coro(graph.checkpointer.aget_tuple(_config("t-evict"))) is not None
        graph.run_coro(graph.checkpointer.adelete_thread("t-evict"))
        after = graph.run_coro(graph.checkpointer.aget_tuple(_config("t-evict")))
    finally:
        graph.close()
    assert after is None, "the thread survived eviction, so the harness database only grows"
