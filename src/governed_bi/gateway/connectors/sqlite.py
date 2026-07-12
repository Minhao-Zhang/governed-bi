"""SQLite connector (dev / BIRD). Uses the stdlib ``sqlite3`` module.

Opens the database **read-only** by default (via a `mode=ro` URI), so any write
attempt raises. This is the connector used against the committed BIRD fixture
and in tests.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from .base import ColumnInfo, Connector, Dialect, QueryResult, TableInfo


def _ident(name: str) -> str:
    """Quote a SQL identifier (defensive, even though names come from the catalog)."""
    return '"' + name.replace('"', '""') + '"'


class SqliteConnector(Connector):
    dialect = Dialect.sqlite

    def __init__(self, path: str | Path, *, read_only: bool = True) -> None:
        self.path = Path(path)
        is_memory = str(self.path) == ":memory:"
        if read_only and not is_memory and not self.path.exists():
            # Do not silently create a new DB when a read-only open was requested.
            raise FileNotFoundError(f"database not found: {self.path}")
        self._conn = sqlite3.connect(":memory:" if is_memory else str(self.path))
        if read_only:
            # Enforced by SQLite; writes then raise. Portable across platforms,
            # unlike a mode=ro file URI with Windows drive-letter paths.
            self._conn.execute("PRAGMA query_only = ON")

    # SQLite has no schema level; the attached default database is "main".
    _NAMESPACE = "main"

    def list_schemas(self) -> list[str]:
        """SQLite has no schemas, so report one logical namespace (``main``).

        Lets schema-aware callers treat SQLite uniformly without special-casing;
        the ``schema`` parameter on every introspection method is accept-and-ignore.
        """
        return [self._NAMESPACE]

    def list_tables(self, schema: str | None = None) -> list[str]:
        # schema: accepted for interface parity; SQLite has no schema level.
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [row[0] for row in cur.fetchall()]

    def describe_table(self, table: str, schema: str | None = None) -> TableInfo:
        cur = self._conn.execute(f"PRAGMA table_info({_ident(table)})")
        rows = cur.fetchall()  # (cid, name, type, notnull, dflt_value, pk)
        if not rows:
            raise ValueError(f"table not found: {table}")
        columns = [
            ColumnInfo(
                name=r[1],
                data_type=(r[2] or ""),
                nullable=(r[3] == 0),
                primary_key=(r[5] > 0),
            )
            for r in rows
        ]
        return TableInfo(name=table, columns=columns)

    def row_count(self, table: str, schema: str | None = None) -> int:
        cur = self._conn.execute(f"SELECT COUNT(*) FROM {_ident(table)}")
        return int(cur.fetchone()[0])

    def sample_values(
        self, table: str, column: str, *, limit: int = 5, schema: str | None = None
    ) -> list[Any]:
        # Plain LIMIT: first N rows, no DISTINCT/NOT NULL — stops immediately, no scan.
        cur = self._conn.execute(
            f"SELECT {_ident(column)} FROM {_ident(table)} LIMIT ?",
            (limit,),
        )
        return [row[0] for row in cur.fetchall()]

    def is_unique(self, table: str, column: str, schema: str | None = None) -> bool:
        cur = self._conn.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT {_ident(column)}) FROM {_ident(table)}"
        )
        total, distinct = cur.fetchone()
        # Non-null values are distinct and cover every row (no nulls). A PK qualifies.
        return total == distinct

    def execute(self, sql: str, *, max_rows: int = 1000, timeout_s: float = 30.0) -> QueryResult:
        start = time.monotonic()

        def _deadline() -> int:
            # Return non-zero to abort the running statement once past the deadline.
            return 1 if (time.monotonic() - start) > timeout_s else 0

        self._conn.set_progress_handler(_deadline, 1000)
        try:
            cur = self._conn.execute(sql)  # read-only connection -> writes raise
            columns = [d[0] for d in cur.description] if cur.description else []
            fetched = cur.fetchmany(max_rows + 1)
            truncated = len(fetched) > max_rows
            rows = [tuple(r) for r in fetched[:max_rows]]
            return QueryResult(columns=columns, rows=rows, row_count=len(rows), truncated=truncated)
        finally:
            self._conn.set_progress_handler(None, 0)

    def explain(self, sql: str) -> str:
        cur = self._conn.execute(f"EXPLAIN QUERY PLAN {sql}")
        return "\n".join(str(tuple(r)) for r in cur.fetchall())

    def close(self) -> None:
        self._conn.close()
