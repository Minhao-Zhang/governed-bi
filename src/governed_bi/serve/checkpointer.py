"""Durable SQLite savers for served conversations and for the CLI and eval. See ADR 0014.

The harness saver must be opened on the loop that will use it. Reusing one across
``asyncio.run`` calls hangs once a lock is contended, and the sync saver does not
implement the async methods LangGraph calls.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

# Absolute import: the server loads this file by path, so it has no parent package.
from governed_bi.paths import REPO_ROOT, assert_not_a_warehouse

__all__ = [
    "assert_not_a_warehouse",
    "conversation_checkpointer",
    "open_harness_saver",
]


def _db_path(setting: str) -> Path:
    """Relative paths start at the repo, then :func:`assert_not_a_warehouse`."""
    from governed_bi.registry.settings import effective_settings

    raw = str(effective_settings()[setting])
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return Path(assert_not_a_warehouse(str(path), source=setting))


def conversation_db() -> Path:
    """Served conversations, resolved at call time so a test can point the setting elsewhere."""
    return _db_path("conversation_db")


def harness_db() -> Path:
    """CLI and eval threads. Separate from the served store. See ADR 0014."""
    return _db_path("harness_db")


# Callers still import CONVERSATION_DB and HARNESS_DB; resolve them at use, not import.
def __getattr__(name: str) -> Path:
    if name == "CONVERSATION_DB":
        return conversation_db()
    if name == "HARNESS_DB":
        return harness_db()
    raise AttributeError(name)


async def _open(path: Path) -> Any:
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    path.parent.mkdir(parents=True, exist_ok=True)
    # Busy timeout is 30s: one whole-state blob per worker against one file, and the
    # default 5s surfaces as a crashed turn. See ADR 0014.
    # `check_same_thread=False` is untested to remove on the concurrent eval path.
    conn = await aiosqlite.connect(str(path), check_same_thread=False, timeout=30)
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    # Under WAL, NORMAL drops an fsync per commit. Loss is the last commits on OS crash.
    await conn.execute("PRAGMA synchronous=NORMAL")
    return saver


@asynccontextmanager
async def conversation_checkpointer() -> AsyncIterator[Any]:
    """Served saver named by ``langgraph.json``. See ADR 0014.

    ``from_conn_string`` takes no connect kwargs, so this uses :func:`_open` and the
    served path gets the same timeout and pragma as the harness.
    """
    saver = await _open(conversation_db())
    try:
        yield saver
    finally:
        await saver.conn.close()


async def open_harness_saver(path: Path | None = None) -> Any:
    """CLI/eval saver. Await it in the owning loop. See ADR 0014 §5."""
    return await _open(path or harness_db())
