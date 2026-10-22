"""Classify on ``sqlite_errorname``, not the message. Prose matching read "no such function"
and ``SQLITE_READONLY`` (a refused write) as infrastructure.
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

#: Prefixes, so ``SQLITE_IOERR_READ`` stays with ``SQLITE_IOERR``. An unlisted code is a
#: query fault: an unknown failure then reads as a bad statement, which can be retried,
#: rather than an outage. A missing code on ``OperationalError`` is still infrastructure.
_INFRASTRUCTURE_CODES: tuple[str, ...] = (
    "SQLITE_BUSY",
    "SQLITE_LOCKED",
    "SQLITE_IOERR",
    "SQLITE_CANTOPEN",
    "SQLITE_CORRUPT",
    "SQLITE_NOTADB",
    "SQLITE_FULL",
    "SQLITE_NOMEM",
    "SQLITE_PROTOCOL",
    "SQLITE_INTERRUPT",
)


class SqliteConnector:
    """Opened ``mode=ro``. ``PRAGMA query_only`` is set when this build accepts it."""

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

    @property
    def endpoint(self) -> dict[str, Any]:
        """File name only, not the absolute path. Keys match ``PostgresConnector.endpoint``.
        """
        return {"database": self._path.name}

    def _connect(self) -> sqlite3.Connection:
        """A new connection per call, unless a ``with`` block already holds ``self._conn``.
        ``sqlite3`` handles are thread-affine, and LangGraph runs ``run_query`` on a worker,
        so a handle kept across calls raises ``ProgrammingError``. A lock would serialise
        every call, and ``check_same_thread=False`` is unsafe. A read-only file open is cheap.
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
        # This call opened `conn` when no `with` block had cached one, and then must close it.
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
        """One map for ``execute`` and ``introspect``. A raw ``sqlite3.Error`` is neither fault.
        """
        code = getattr(err, "sqlite_errorname", None)
        if isinstance(code, str) and code.startswith(_INFRASTRUCTURE_CODES):
            raise ConnectionError(str(err), sqlstate=code) from err
        if code is None and isinstance(err, sqlite3.OperationalError):
            # No code: an older interpreter, or the error was raised before prepare.
            # ``OperationalError`` then matches Postgres: not the caller's fault.
            raise ConnectionError(str(err)) from err
        raise QueryError(str(err), sqlstate=code if isinstance(code, str) else None) from err

    def introspect(self) -> Introspection:
        # `conn` and `owns_conn` stay outside `try`. If `_connect()` raises, `finally`
        # must not turn that into a `NameError` by reading names that were never bound.
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


def _quote(name: str) -> str:
    """Quote-doubled. ``PRAGMA`` takes no parameters, so the identifier cannot be bound."""
    return '"' + name.replace('"', '""') + '"'
