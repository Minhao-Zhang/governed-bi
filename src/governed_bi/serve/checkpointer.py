"""Durable checkpoint stores: one for served conversations, one for the CLI and eval.

The served store replaces the dev server's default saver -- pickle shards under
``.langgraph_api/`` flushed by a daemon thread every ten seconds -- with a SQLite database the
server opens and closes on its lifespan. Mounted by ``langgraph.json``'s ``checkpointer.path``;
LangGraph Server owns its lifecycle.

**Its job is one thing: a conversation survives a restart.** It does *not* feed the audit surface,
and an earlier version of this docstring drew that consequence anyway. ``Threads.search`` never
reaches a checkpointer -- the source of ``langgraph_runtime_inmem.ops.Threads.search`` contains no
such reference (verified 2026-08-20 at ``langgraph-runtime-inmem`` 0.32.3) -- so ``/audit/turns``
reads the *thread row's* ``values``, which that runtime copies out of ``checkpoint["values"]`` at
run completion (``ops.py:1184``, ``:1282``) into ``.langgraph_api/.langgraph_ops.pckl``, a
``PersistentDict`` flushed by a daemon thread every ten seconds (``_persistence.py:17``, ``:53``)
and on ``stop_pool``. Thread ``status`` and ``interrupts`` live in the same pickle, so the pending
clarification queue reads it too. ``api/thread_turns.ThreadTurnLog.TURN_LOG_DIR`` is the account of
this; these two must agree.

**The durable half and the read half are different halves**, which is what a reader needs next.
Measured 2026-08-20: ``runs/conversations.sqlite`` holds 88.4 MB of checkpoints nothing on the audit
path opens, while the 2.7 MB ``.langgraph_ops.pckl`` it does read is a *disposable cache* to its
owner -- ``GLOBAL_STORE.load()`` deletes it on ``ModuleNotFoundError`` **and** on a bare
``Exception`` (``database.py:167-184``; the log text names "Renamed or moved classes"). The two
failures point opposite ways: a hard kill inside the ten-second flush window loses a paused
clarification from the thread registry while its checkpoint is already durable here, and a module
rename destroys the audit history while these checkpoints survive unread. Nothing expires either --
``langgraph.json`` configures ``checkpointer.ttl``, but ``langgraph_runtime_inmem``'s ``sweep_ttl``
is ``return (0, 0)`` and no caller exists in ``site-packages``, so this file grows monotonically
(``runs/conversations.sqlite``: 0 freelist pages, measured 2026-08-20). ADR 0014 §4.

That a conversation survives a restart was watched by hand on 2026-08-19: a clarification paused,
the process was killed with nothing left listening, a fresh one re-mounted the prompt from
checkpointed interrupt state, and answering it resumed the turn to a correct answer
(``git-history:docs/analysis/adopting-the-downstream-fork-2026-08-19.md``).
``tests/serve/test_a_pause_survives_a_restart_on_disk.py`` has driven that path since 2026-08-20:
a real ``ask_user`` interrupt written onto this saver through ``graph.compile_durable``, then
answered by a second graph over the same file. What it does **not** cross is a process boundary --
its own header says so -- so the interpreter dying is still covered only by the hand run above.

**Two databases, one mechanism.** :data:`CONVERSATION_DB` holds served conversations. The CLI and
eval get :data:`HARNESS_DB` instead, because their traffic is measurement. Measured 2026-08-20: a
checkpoint blob averages 82.5 KB here (900 checkpoints, 36 threads, 88.4 MB) and 76.6 KB in
``runs/harness-checkpoints.sqlite``, where one single-turn thread is 23 checkpoints and ~2.1 MB --
so a 131-question arm is ~275 MB, three times the whole conversation store as it stands, and one
file would be mostly benchmark. That is the same contamination that put 116 ``t-transport`` turns
into the old JSONL log with no field to tell them apart. (This docstring used to price the arm at
"~3.9 MB of checkpoint each"; ADR 0014's Consequences retracted that as turn one's cost read as a  [retired]
constant, and measured off the dev server's per-channel pickle rather than this saver.)

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
from governed_bi.paths import REPO_ROOT, assert_not_a_warehouse

__all__ = [
    "CONVERSATION_DB",
    "HARNESS_DB",
    "assert_not_a_warehouse",
    "conversation_checkpointer",
    "open_harness_saver",
]


#: Markers of a *server* connection string. A checkpointer takes a filesystem path, so any of
#: these means the operator handed us a warehouse DSN -- the one thing this must never be.
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
