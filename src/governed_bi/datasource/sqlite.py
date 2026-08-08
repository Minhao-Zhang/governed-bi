"""SQLite connector adapter (dev path and the parcel C acceptance suite).

**Classification is on SQLite's own result code, not on the message** (audit §9.2).
``sqlite3.Error.sqlite_errorname`` (Python 3.11+) is this engine's SQLSTATE: a closed
vocabulary the driver assigns, rather than a sentence it may reword. The prose regex it
replaced misclassified two verified faults — *no such function* read as the database
being unreachable, and ``SQLITE_READONLY`` (governance stopping a write) recorded as
infrastructure being down, which hides a working control.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Sequence

from ..corpus.introspect import (
    ForeignKeyInfo,
    IntrospectedColumn,
    IntrospectedTable,
    Introspection,
)
from .errors import ConnectionError, QueryError

__all__ = ["SqliteConnector"]

_DEFAULT_MAX_ROWS = 200_000

#: Result-code prefixes that mean **the store was unusable**, not the statement wrong.
#:
#: Prefixes because SQLite extends its primary codes (``SQLITE_IOERR_READ``,
#: ``SQLITE_BUSY_SNAPSHOT``) and a new extended code must land on the same side as its base.
#:
#: Everything not listed classifies as a query fault, matching the Postgres adapter's
#: statement path. Deliberately the safe direction: an unanticipated code degrades to "the
#: statement was wrong", which is recoverable, where the reverse makes a real outage look
#: like a model error (§9.1).
_INFRASTRUCTURE_CODES: tuple[str, ...] = (
    "SQLITE_BUSY",      # another writer holds the lock
    "SQLITE_LOCKED",    # a table in this connection is locked
    "SQLITE_IOERR",     # disk read/write failure
    "SQLITE_CANTOPEN",  # the file could not be opened
    "SQLITE_CORRUPT",   # the image is malformed
    "SQLITE_NOTADB",    # not a database file
    "SQLITE_FULL",      # the disk is full
    "SQLITE_NOMEM",     # allocation failed
    "SQLITE_PROTOCOL",  # locking protocol error
    "SQLITE_INTERRUPT", # interrupted by sqlite3_interrupt
)


class SqliteConnector:
    """Read-only-ish SQLite access. ``PRAGMA query_only`` when the build supports it."""

    def __init__(self, path: str | Path, *, max_rows: int = _DEFAULT_MAX_ROWS) -> None:
        self._path = Path(path)
        self._max_rows = max_rows
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> SqliteConnector:
        self._connect()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def dialect(self) -> str:
        return "sqlite"

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        if not self._path.exists():
            raise ConnectionError(f"database file does not exist: {self._path}")
        try:
            conn = sqlite3.connect(f"file:{self._path.as_posix()}?mode=ro", uri=True)
        except sqlite3.Error as err:
            raise ConnectionError(str(err)) from err
        try:
            conn.execute("PRAGMA query_only = ON")
        except sqlite3.Error:
            pass
        self._conn = conn
        return conn

    def execute(
        self, sql: str, *, max_rows: int | None = None
    ) -> tuple[Sequence[str], Sequence[tuple[Any, ...]], bool]:
        cap = self._max_rows if max_rows is None else max_rows
        try:
            conn = self._connect()
            cur = conn.execute(sql)
            columns = [d[0] for d in (cur.description or ())]
            rows = cur.fetchmany(cap + 1)
        except sqlite3.Error as err:
            self._raise_classified(err)
        truncated = len(rows) > cap
        if truncated:
            rows = rows[:cap]
        return columns, rows, truncated

    def _raise_classified(self, err: sqlite3.Error) -> None:
        """Map a driver fault by result code. One classifier, so both callers agree.

        ``introspect`` must route through here too: letting ``sqlite3.Error`` escape raw
        leaves a failure neither a query fault nor infrastructure, which the Postgres
        adapter's ``test_introspection_classifies_its_own_failures`` forbids.
        """
        code = getattr(err, "sqlite_errorname", None)
        if isinstance(code, str) and code.startswith(_INFRASTRUCTURE_CODES):
            raise ConnectionError(str(err), sqlstate=code) from err
        if code is None and isinstance(err, sqlite3.OperationalError):
            # No result code: an older interpreter, or an error raised before a statement
            # was prepared. ``OperationalError`` without one is the DB-API's "not under the
            # programmer's control", the same reading the Postgres adapter takes.
            raise ConnectionError(str(err)) from err
        raise QueryError(str(err), sqlstate=code if isinstance(code, str) else None) from err

    def introspect(self) -> Introspection:
        # Classified like ``execute``: an unclassified failure is neither a query fault nor
        # infrastructure, so nothing downstream can decide whether to retry.
        try:
            conn = self._connect()
            table_names = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            tables: list[IntrospectedTable] = []
            fks: list[ForeignKeyInfo] = []
            for name in table_names:
                cols = tuple(
                    IntrospectedColumn(
                        physical_name=row[1],
                        physical_type=(row[2] or "TEXT").upper(),
                        nullable=not row[3],
                    )
                    for row in conn.execute(f"PRAGMA table_info({_quote(name)})")
                )
                tables.append(IntrospectedTable(physical_name=name, columns=cols))
                for row in conn.execute(f"PRAGMA foreign_key_list({_quote(name)})"):
                    # id, seq, table, from, to, on_update, on_delete, match
                    fks.append(
                        ForeignKeyInfo(
                            from_table=name,
                            from_columns=(row[3],),
                            to_table=row[2],
                            to_columns=(row[4],),
                        )
                    )
        except (QueryError, ConnectionError):
            raise
        except sqlite3.Error as err:
            self._raise_classified(err)
        return Introspection(tables=tuple(tables), foreign_keys=tuple(fks))

    def list_tables(self) -> Sequence[str]:
        return [t.physical_name for t in self.introspect().tables]

    def describe_table(self, name: str) -> IntrospectedTable:
        for table in self.introspect().tables:
            if table.physical_name == name:
                return table
        raise QueryError(f"no such table: {name}")

    # ``sample_values`` was here and is gone; see ``ports.Connector``. This adapter quoted
    # correctly and its Postgres sibling did not: one port method, two implementations, and
    # the security property held in only one of them.


def _quote(name: str) -> str:
    """A SQLite identifier, quote-doubled. Only for ``PRAGMA``, which takes no parameters."""
    return '"' + name.replace('"', '""') + '"'
