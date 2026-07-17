"""SQLite connector (dev / BIRD). Uses the stdlib ``sqlite3`` module.

Opens the database **read-only** by default (``PRAGMA query_only``), so any write
attempt raises. This is the connector used against the committed BIRD fixture
and in tests.

SQLite has no native schema level, but the whole engine is now uniformly
schema-qualified (``schema.table`` everywhere; the ``multi_schema`` flag is gone).
So the connector fakes a schema: it opens the file as ``main`` **and** ATTACHes
the same file under a schema alias (the ``schema`` argument, defaulting to the
file-name stem — the BIRD ``db_id``). Then a generated ``beer_factory.customers``
resolves against the attachment, while bare / ``main`` references still resolve
to the same data. This mirrors Postgres, where the schema is the ``db_id``, and
generalises to attaching several files as several schemas later.
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

    def __init__(
        self, path: str | Path, *, schema: str | None = None, read_only: bool = True
    ) -> None:
        self.path = Path(path)
        is_memory = str(self.path) == ":memory:"
        if read_only and not is_memory and not self.path.exists():
            # Do not silently create a new DB when a read-only open was requested.
            raise FileNotFoundError(f"database not found: {self.path}")
        # The fake schema alias: the caller's ``schema``, else the file-name stem
        # (the BIRD ``db_id``, matching the corpus's ``schema`` field). ``:memory:``
        # has no stem, so it stays the native ``main``.
        self._schema = schema or (self._NAMESPACE if is_memory else self.path.stem)
        self._conn = sqlite3.connect(
            ":memory:" if is_memory else str(self.path),
            # LangGraph ToolNode runs tools on a worker thread; allow the same
            # connection (we still serialize tool calls via parallel_tool_calls=False).
            check_same_thread=False,
        )
        if read_only:
            # Enforced by SQLite; writes then raise. Connection-wide, so it also
            # covers the attached schema below. Portable across platforms, unlike a
            # mode=ro file URI with Windows drive-letter paths.
            self._conn.execute("PRAGMA query_only = ON")
        # ATTACH the same file under the schema alias so ``schema.table`` resolves
        # exactly like Postgres. Skipped for ``:memory:`` (no file) and when the
        # alias is the native ``main`` (already addressable). ``query_only`` above
        # keeps the attachment read-only too.
        if not is_memory and self._schema != self._NAMESPACE:
            self._conn.execute(f"ATTACH DATABASE ? AS {_ident(self._schema)}", (str(self.path),))
            self._assert_usable_qualifier()

    def _assert_usable_qualifier(self) -> None:
        """Fail loudly if the schema alias cannot serve as an UNQUOTED qualifier.

        Generated SQL qualifies bare, ``schema.table`` — so the alias must parse as
        a bare qualifier, not just as a quoted identifier. A reserved word (e.g.
        ``order``) or a dotted stem (``beer.factory`` -> parsed as ``catalog.schema``)
        would silently false-refuse *every* query at run time. Probe it with SQLite's
        own parser and turn that into an actionable construction-time error instead.
        """
        probe = f"SELECT 1 FROM {self._schema}.sqlite_master LIMIT 0"  # alias UNQUOTED, on purpose
        try:
            self._conn.execute(probe).fetchall()
        except sqlite3.OperationalError as err:
            self._conn.close()
            raise ValueError(
                f"schema alias {self._schema!r} is not usable as an unqualified SQL "
                f"qualifier (reserved word or invalid identifier): {err}. Rename the "
                "SQLite file / set an explicit `schema=` to a plain identifier."
            ) from err

    # SQLite has no schema level; the native default database is "main".
    _NAMESPACE = "main"

    def list_schemas(self) -> list[str]:
        """Report the one fake schema this connection serves (the attach alias).

        Lets schema-aware callers treat SQLite uniformly with Postgres; the
        ``schema`` parameter on every introspection method is accept-and-ignore
        (``main`` and the alias are the same file, so introspecting ``main`` is
        correct regardless of which the caller names).
        """
        return [self._schema]

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
