"""Durable checkpoint stores: one for served conversations, one for the CLI and eval.

The served store replaces the dev server's default saver -- pickle shards under
``.langgraph_api/`` flushed by a daemon thread every ten seconds -- with a SQLite database the
server opens and closes on its lifespan. It is what makes a conversation survive a restart, and
therefore what lets the audit surface read turns out of thread state rather than out of a second
JSONL log. Mounted by ``langgraph.json``'s ``checkpointer.path``; LangGraph Server owns its
lifecycle.

**Two databases, one mechanism.** :data:`CONVERSATION_DB` holds served conversations. The CLI and
eval get :data:`HARNESS_DB` instead, because their traffic is measurement: 131 questions at ~3.9 MB
of checkpoint each would make the conversation store mostly benchmark, which is the same
contamination that put 116 ``t-transport`` turns into the old JSONL log with no field to tell them
apart.

**Neither is ever the analytics warehouse.** :func:`assert_not_a_warehouse` refuses a value that
looks like a libpq DSN or a database URL rather than trusting an operator to keep two settings
apart: the facilities Postgres holds real data, and a checkpointer pointed at it would write
conversation state into it on the first turn. Failing at configuration time is worth more than
discovering it while reading the wrong table.

**Why the harness saver is opened inside a caller-owned loop.** The CLI and eval reach the graph
through ``graph._SyncApp``. Reusing an ``AsyncSqliteSaver`` across two ``asyncio.run`` calls does
not raise -- it **hangs** (observed). The cause is the saver's ``asyncio.Lock``, created on the loop
that constructed it and taken by every method: an *uncontended* acquire returns on a fast path that
never inspects the loop, so single-task reuse looks fine, while a *contended* one -- which the facet
fan-out guarantees every turn -- raises ``RuntimeError: Lock ... is bound to a different event
loop`` **and leaves the lock held**, poisoning the saver for good. It is **not** the connection:
``aiosqlite`` creates each future on the current loop and resolves it via
``future.get_loop().call_soon_threadsafe(...)``. That distinction matters because :meth:`close`
closes the *connection*, so simplifying it in the belief that it addressed the loop problem would
not. The sync ``SqliteSaver`` is no escape
either: every node here is ``async def``, so LangGraph calls ``aget_tuple``, and that class raises
``NotImplementedError`` on every async method. So :func:`open_harness_saver` is a coroutine and
``graph.compile_durable`` runs it on the one long-lived loop it then pins to the returned app.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

# Absolute, not relative: the server loads this file **by path** (`_load_checkpointer` calls
# `exec_module` on a spec built from `checkpointer.path`), so it has no parent package and
# `from ..paths import ...` raises `ImportError: attempted relative import with no known parent
# package`. `api/graph_app.py` is loaded the same way and imports absolutely for the same reason.
from governed_bi.paths import REPO_ROOT

__all__ = [
    "CONVERSATION_DB",
    "HARNESS_DB",
    "assert_not_a_warehouse",
    "conversation_checkpointer",
    "open_harness_saver",
]


#: Markers of a *server* connection string. A checkpointer takes a filesystem path, so any of
#: these means the operator handed us a warehouse DSN -- the one thing this must never be.
_WAREHOUSE_MARKERS: tuple[str, ...] = (
    "postgres://",
    "postgresql://",
    "redshift://",
    "host=",
    "dbname=",
    "password=",
)


def assert_not_a_warehouse(value: str, *, source: str) -> str:
    """Return ``value``, or raise if it names a database server rather than a file."""
    lowered = value.lower()
    for marker in _WAREHOUSE_MARKERS:
        if marker in lowered:
            raise ValueError(
                f"{source} looks like a database connection string ({marker!r} in it), not a "
                "file path. Conversation state is checkpointed to its own SQLite file and must "
                "never share a database with the analytics warehouse. Set it to a path such as "
                "'runs/conversations.sqlite'."
            )
    return value


def _db_path(env: str, default_name: str) -> Path:
    raw = os.environ.get(env)
    if raw:
        return Path(assert_not_a_warehouse(raw, source=env))
    return REPO_ROOT / "runs" / default_name


#: Served conversations. Overridable so a test never writes into the real store.
CONVERSATION_DB = _db_path("GOVERNED_BI_CONVERSATION_DB", "conversations.sqlite")

#: CLI + eval threads. Same saver, separate file, so a benchmark never lands in the transcript.
HARNESS_DB = _db_path("GOVERNED_BI_HARNESS_DB", "harness-checkpoints.sqlite")


async def _open(path: Path) -> Any:
    """Open an ``AsyncSqliteSaver`` on ``path`` in the *current* loop, tables created."""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    path.parent.mkdir(parents=True, exist_ok=True)
    # `timeout` is SQLite's **busy timeout** and it defaults to 5 s. Every commit here carries a
    # whole-state blob (this saver has no per-channel table, so one super-step writes the entire
    # checkpoint), and eval runs one writer per worker against one file, so 5 s is thin. 30 s is
    # cheap insurance against a `database is locked` that would surface as a crashed turn.
    #
    # `check_same_thread=False` is kept but is probably unnecessary: `aiosqlite` dispatches the
    # connector onto its own worker thread, so the `sqlite3.Connection` is created and used on one
    # thread whatever this says. It is *not* kept for the reason the comment here used to give
    # ("eval compiles one graph per worker thread against one saver") -- that is false, each worker
    # gets its own graph, loop and connection, and per the note above it must. Left in place only
    # because removing a defensive flag on the concurrent eval path, which no test exercises, is
    # the worse trade; delete it once an arm has actually run.
    conn = await aiosqlite.connect(str(path), check_same_thread=False, timeout=30)
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    # WAL is already on (`setup()` runs the pragma). `synchronous=NORMAL` is the matching half and
    # matters more here than usual: under WAL it drops an fsync per commit, and the commits are
    # multi-megabyte. Durability cost is bounded to losing the last commits on OS crash, not on
    # process crash -- acceptable for a conversation store, and the alternative is paying a full
    # fsync ~15 times per turn.
    await conn.execute("PRAGMA synchronous=NORMAL")
    return saver


@asynccontextmanager
async def conversation_checkpointer() -> AsyncIterator[Any]:
    """Yield the served path's saver. Named by ``langgraph.json``'s ``checkpointer.path``.

    An async context manager because that is the shape the server asks for: it opens the
    connection on startup and closes it on shutdown, so nothing here owns a process-wide handle.

    Opened through :func:`_open` and **not** ``AsyncSqliteSaver.from_conn_string``, which takes no
    connect arguments (``aiosqlite.connect(conn_string)``, no kwargs). Using it here meant the
    served path -- the one that matters -- got neither the busy timeout nor the ``synchronous``
    pragma that :func:`_open` sets, while the harness path got both. One opener, so the two cannot
    diverge again.
    """
    saver = await _open(CONVERSATION_DB)
    try:
        yield saver
    finally:
        await saver.conn.close()


async def open_harness_saver(path: Path | None = None) -> Any:
    """The CLI/eval saver, on :data:`HARNESS_DB` by default. Await it *in the owning loop*.

    Returned rather than yielded: the caller's graph outlives any ``async with`` this could sit
    in, and the connection is closed by process exit. ``graph.compile_durable`` is the intended
    caller and pins the loop it was opened on.
    """
    return await _open(path or HARNESS_DB)
