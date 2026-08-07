"""SQLite connector adapter (dev path and the parcel C acceptance suite).

**Classification is on SQLite's own result code, not on the message** (audit §9.2). It was a
prose regex — ``no such column|no such table|syntax error|ambiguous column``, anything else
``ConnectionError`` — and it misclassified two faults that matter, both verified:

* ``SELECT connection_is_not_a_function_xyz(1)`` raises *no such function*, which no marker
  matches, so a query fault was reported as the database being unreachable. That statement is
  the literal example ``test_classification_reads_the_code_not_the_message`` uses to assert the
  classifier reads a code and not prose — and that test is Postgres-gated, so it never ran
  against the adapter that fails it.
* A write on the read-only connection raises ``SQLITE_READONLY``, also unmatched. So
  **governance stopping a write was recorded as infrastructure being down**, which is the
  crash-counted-as-refusal inversion in the one direction that hides a working control.

``sqlite3.Error.sqlite_errorname`` (Python 3.11+) is this engine's SQLSTATE: a closed
vocabulary the driver assigns, rather than a sentence it may reword.
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
#: Everything not listed classifies as a query fault, matching the Postgres adapter's rule for
#: its statement path: an engine that answered at all answered about the statement. The set is
#: small and closed, so a code nobody thought about degrades to "the statement was wrong", which
#: is recoverable — the reverse would make a real outage look like a model error, which is the
#: defect §9.1 documents on the other adapter.
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
        self._conn = self._connect()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def dialect(self) -> str:
        return "sqlite"

    def _connect(self) -> sqlite3.Connection:
        """Open a connection for **this call**. Never cached across calls.

        A memoized ``self._conn`` used to survive from whichever call first populated it,
        and ``sqlite3`` enforces thread affinity on the object it returns: a later call from
        a different thread raised ``ProgrammingError: SQLite objects created in a thread can
        only be used in that same thread``. LangGraph's node executor runs a tool call (e.g.
        ``run_query``) in its own worker thread per invocation, so the second call after
        construction — from any caller on any thread but the first — was the common case, not
        an edge case, and it crashed instead of erroring cleanly (RESUME.md, 2026-08-06: two
        independent 15+-minute hangs, both root-caused here).

        A read-only local-file connection is cheap to open, which is exactly what makes
        opening a fresh one per call the fix rather than a workaround: the alternative is
        either a lock (serializing every call through one thread) or ``check_same_thread=
        False`` (silently permitting concurrent access sqlite3's own docs call unsafe).
        Neither buys anything a fresh connection does not already have for free here.

        Kept as an instance method (rather than a bare function) for ``__enter__``/
        ``__exit__``, which still track one connection across the `with` block's lifetime —
        that scope is single-threaded by construction, so caching there was never the
        problem.
        """
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
        return conn

    def execute(
        self, sql: str, *, max_rows: int | None = None
    ) -> tuple[Sequence[str], Sequence[tuple[Any, ...]], bool]:
        cap = self._max_rows if max_rows is None else max_rows
        conn = self._connect()
        # Whether *this call* opened `conn` (and so owes closing it) or is borrowing the one
        # a `with` block cached. `self._conn` is unread and unwritten anywhere else between
        # here and the check, so this is exactly "was there already a cached connection".
        owns_conn = self._conn is None
        try:
            cur = conn.execute(sql)
            columns = [d[0] for d in (cur.description or ())]
            rows = cur.fetchmany(cap + 1)
        except sqlite3.Error as err:
            self._raise_classified(err)
        finally:
            if owns_conn:
                conn.close()
        truncated = len(rows) > cap
        if truncated:
            rows = rows[:cap]
        return columns, rows, truncated

    def _raise_classified(self, err: sqlite3.Error) -> None:
        """Map a driver fault by result code. One classifier, so both callers agree.

        ``introspect`` did not classify at all — it let ``sqlite3.Error`` escape raw, so a
        failure there was neither a query fault nor infrastructure but unclassified, which is
        exactly what the Postgres adapter's ``test_introspection_classifies_its_own_failures``
        exists to forbid.
        """
        code = getattr(err, "sqlite_errorname", None)
        if isinstance(code, str) and code.startswith(_INFRASTRUCTURE_CODES):
            raise ConnectionError(str(err), sqlstate=code) from err
        if code is None and isinstance(err, sqlite3.OperationalError):
            # No result code (an older interpreter, or an error raised before a statement was
            # prepared). ``OperationalError`` without one is the DB-API's "not under the
            # programmer's control", which is the same reading the Postgres adapter takes.
            raise ConnectionError(str(err)) from err
        raise QueryError(str(err), sqlstate=code if isinstance(code, str) else None) from err

    def introspect(self) -> Introspection:
        # Classified like ``execute``, rather than letting ``sqlite3.Error`` escape raw. An
        # unclassified failure here is neither a query fault nor infrastructure, so nothing
        # downstream can decide whether to retry or to record a bad statement.
        conn = self._connect()
        owns_conn = self._conn is None  # see execute()'s comment
        try:
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
        finally:
            if owns_conn:
                conn.close()
        return Introspection(tables=tuple(tables), foreign_keys=tuple(fks))

    def list_tables(self) -> Sequence[str]:
        return [t.physical_name for t in self.introspect().tables]

    def describe_table(self, name: str) -> IntrospectedTable:
        for table in self.introspect().tables:
            if table.physical_name == name:
                return table
        raise QueryError(f"no such table: {name}")

    # ``sample_values`` was here and is gone; see ``ports.Connector``. This adapter's version
    # quoted correctly and its Postgres sibling did not, which is the argument against having
    # the method at all: one port method, two implementations, and the security property held
    # in only one of them, with no test on either.


def _quote(name: str) -> str:
    """A SQLite identifier, quote-doubled. Only for ``PRAGMA``, which takes no parameters."""
    return '"' + name.replace('"', '""') + '"'
