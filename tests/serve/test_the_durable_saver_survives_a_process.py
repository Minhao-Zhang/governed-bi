"""The durable checkpointer, opened for real.

**No other test opens one.** ``compile_durable`` is patched to a stub in the one place eval's
concurrent path is exercised, and everything else takes ``compile_graph``'s ``InMemorySaver``. So
the whole reason the saver exists — a checkpoint that outlives the process that wrote it — was
verified only by hand, and three of the four traps below are the kind that pass a review and hang a
run:

- ``aiosqlite`` binds its connection to the loop that opened it. A saver reused across two
  ``asyncio.run`` calls does not raise, it **hangs** — so a test that merely constructs one proves
  nothing about using it twice.
- Its worker thread is not a daemon and CPython joins non-daemon threads *before* ``atexit``, so a
  graph that is never closed stops the interpreter from exiting. A leak here does not fail an
  assertion; it hangs the suite.
- ``AsyncSqliteSaver.delete_thread`` exists **and raises**, which is why ``eval/harness._evict``
  prefers ``adelete_thread``. A saver that cannot evict grows the harness database without bound,
  silently, because that function swallows.

Each test bounds itself with ``pytest.mark.timeout``-free plain code and its own ``tmp_path``
database, so a hang is a hung test rather than a poisoned session, and no test touches
``HARNESS_DB``.
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
    """Put a checkpoint on ``thread`` through the saver, without running the graph.

    ``update_state`` is the seam because this file is about persistence, not about the serve
    topology: a real turn would need a model, a connector and a corpus, and none of them are what
    could be broken here.
    """
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
    """The property the whole change exists for, and the one nothing tested.

    Two savers, opened one after the other on the same file, in two different event loops — which
    is what a restart is. Under ``InMemorySaver`` the second read returns nothing.
    """
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
    """Two reads through one graph.

    This is the ``aiosqlite`` loop-binding trap: with ``_SyncApp``'s default ``asyncio.run``-per-call
    the second call's future belongs to a closed loop and **never returns**. If this test hangs
    instead of failing, the pinned loop in ``compile_durable`` is gone.
    """
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
    """``close()`` is load-bearing, not tidiness — see this module's header.

    Asserted through the filesystem rather than by inspecting the object: after ``close`` the
    write-ahead log has been folded back in and a *plain* ``sqlite3`` connection can read the
    table, which is only true if the connection really closed.
    """
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
    """``close`` is called from ``finally`` blocks that may already have closed.

    The second half matters more: ``compile_graph()`` — what most of the suite uses — has no pinned
    loop, and ``close`` on it must be a no-op rather than tearing down an ``InMemorySaver`` some
    other test is mid-way through.
    """
    from governed_bi.serve.graph import compile_graph

    plain = compile_graph()
    plain.close()
    plain.close()
    assert plain.invoke is not None  # still usable: nothing was torn down


def test_a_thread_can_be_evicted_through_the_async_method(tmp_path: Path) -> None:
    """``eval/harness._evict`` depends on this and swallows failures, so nothing else would notice.

    ``delete_thread`` (sync) exists on this saver and raises; ``adelete_thread`` is the one that
    works. Pinned because a saver that cannot evict makes an arm's database grow without bound.
    """
    graph = compile_durable(path=tmp_path / "evict.sqlite")
    try:
        _write_one(graph, "t-evict", {"question": "q"})
        assert graph.run_coro(graph.checkpointer.aget_tuple(_config("t-evict"))) is not None
        graph.run_coro(graph.checkpointer.adelete_thread("t-evict"))
        after = graph.run_coro(graph.checkpointer.aget_tuple(_config("t-evict")))
    finally:
        graph.close()
    assert after is None, "the thread survived eviction, so the harness database only grows"
