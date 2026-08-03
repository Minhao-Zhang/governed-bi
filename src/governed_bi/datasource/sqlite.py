"""SQLite connector adapter (dev path and the parcel C acceptance suite)."""

from __future__ import annotations

import re
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
_QUERY_MARKERS = re.compile(
    r"no such column|no such table|syntax error|ambiguous column",
    re.IGNORECASE,
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
        except sqlite3.OperationalError as err:
            message = str(err)
            if _QUERY_MARKERS.search(message):
                raise QueryError(message) from err
            raise ConnectionError(message) from err
        except sqlite3.Error as err:
            raise QueryError(str(err)) from err
        truncated = len(rows) > cap
        if truncated:
            rows = rows[:cap]
        return columns, rows, truncated

    def introspect(self) -> Introspection:
        conn = self._connect()
        table_names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
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
        return Introspection(tables=tuple(tables), foreign_keys=tuple(fks))

    def list_tables(self) -> Sequence[str]:
        return [t.physical_name for t in self.introspect().tables]

    def describe_table(self, name: str) -> IntrospectedTable:
        for table in self.introspect().tables:
            if table.physical_name == name:
                return table
        raise QueryError(f"no such table: {name}")

    def sample_values(self, table: str, column: str, *, limit: int) -> Sequence[Any]:
        sql = (
            f"SELECT DISTINCT {_quote(column)} FROM {_quote(table)} "
            f"WHERE {_quote(column)} IS NOT NULL "
            f"ORDER BY {_quote(column)} LIMIT {int(limit)}"
        )
        _, rows, _ = self.execute(sql, max_rows=limit)
        return [row[0] for row in rows]


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
