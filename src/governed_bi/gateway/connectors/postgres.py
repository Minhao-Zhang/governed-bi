"""Postgres connector: read-only boundary over ``information_schema`` + psycopg.

Catalog introspection (``list_tables`` / ``describe_table`` / uniqueness / samples)
reads ``information_schema`` views, which is portable across Postgres-wire-protocol
engines — the ``RedshiftConnector`` subclass reuses this class and overrides only
the introspection seams (``list_tables``, ``_column_specs``, ``_primary_keys``)
with Redshift's ``svv_*`` views. Guarded execution (``execute``) sets a
per-statement ``statement_timeout`` and applies a forced row cap + truncation
flag, matching the ``sqlite.py`` reference connector's fetch-and-truncate
pattern.

Read-only is security-critical: the gateway trusts this connector to make writes
fail, not the reverse. ``psycopg`` (v3) makes every subsequent transaction on a
connection read-only once ``connection.read_only = True`` is set, so INSERT /
UPDATE / DDL raise at execute time. That in-process guard is belt-and-suspenders
only — production deployments MUST also connect through a read-only DB role /
grant, since an application bug or connector misuse should never be the last
line of defense.

``psycopg`` is imported lazily (see ``_require_psycopg``) so importing this
module — or constructing a ``PostgresConnector`` against an injected
``connection=`` (as the offline unit tests do) — never requires the driver to
be installed. Install the optional extra to open a real connection:

    uv sync --extra postgres
"""

from __future__ import annotations

from typing import Any

from .base import ColumnInfo, Connector, Dialect, QueryResult, TableInfo


def _require_psycopg():
    try:
        import psycopg
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "PostgresConnector needs psycopg; install it: uv sync --extra postgres"
        ) from e
    return psycopg


def _ident(name: str) -> str:
    """Quote a SQL identifier (defensive, even though names come from the catalog)."""
    return '"' + name.replace('"', '""') + '"'


class PostgresConnector(Connector):
    dialect = Dialect.postgres

    def __init__(
        self,
        dsn: str,
        *,
        schema: str | None = None,
        read_only: bool = True,
        connection: Any = None,
        **connect_kwargs: Any,
    ) -> None:
        self.dsn = dsn
        self.read_only = read_only
        self.schema = schema or "public"
        if connection is not None:
            # Connection seam: tests (and callers with their own pooling) inject
            # a pre-built connection instead of dialing out here.
            self._conn = connection
        else:
            self._conn = _require_psycopg().connect(dsn, autocommit=True, **connect_kwargs)
        if read_only:
            # psycopg3: setting this makes every subsequent transaction on the
            # connection read-only, so writes raise. See the module docstring —
            # a read-only DB role is still required in production as the real
            # belt-and-suspenders, this is not a substitute for one.
            self._conn.read_only = True

    def _qualified(self, table: str) -> str:
        return f"{_ident(self.schema)}.{_ident(table)}"

    # -- cursor helpers: keep all cursor usage here so a fake connection is ---
    # -- trivial to test against ---------------------------------------------
    def _fetchall(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return [tuple(r) for r in cur.fetchall()]

    def _fetchone(self, sql: str, params: tuple = ()) -> tuple | None:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    # -- catalog introspection --------------------------------------------- #

    def list_tables(self) -> list[str]:
        rows = self._fetchall(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
            "ORDER BY table_name",
            (self.schema,),
        )
        return [r[0] for r in rows]

    def list_schemas(self) -> list[str]:
        """User schemas (system + temp schemas excluded), one per db_id in the
        BIRD-Obfuscation instances. Postgres-specific: SQLite has no schema level.
        """
        # ``%%`` escapes the literal percent: _fetchall runs the parameterized
        # path, where a bare ``%`` would be misread as a placeholder.
        rows = self._fetchall(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
            "AND schema_name NOT LIKE 'pg_temp_%%' "
            "AND schema_name NOT LIKE 'pg_toast_temp_%%' "
            "ORDER BY schema_name"
        )
        return [r[0] for r in rows]

    def _column_specs(self, table: str) -> list[tuple[str, str, bool]]:
        """(name, raw data type, nullable) per column. Seam: Redshift overrides
        this for ``svv_*`` views; keep the SQL here only, not inlined elsewhere.
        """
        rows = self._fetchall(
            "SELECT column_name, data_type, is_nullable, character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position",
            (self.schema, table),
        )
        specs: list[tuple[str, str, bool]] = []
        for name, data_type, is_nullable, char_len in rows:
            raw_type = f"{data_type}({char_len})" if char_len is not None else data_type
            specs.append((name, raw_type, is_nullable == "YES"))
        return specs

    def _primary_keys(self, table: str) -> set[str]:
        """Primary-key column names. Seam: Redshift overrides this for ``svv_*``
        views; keep the SQL here only, not inlined elsewhere.
        """
        rows = self._fetchall(
            "SELECT kcu.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_name = kcu.constraint_name "
            "AND tc.table_schema = kcu.table_schema "
            "WHERE tc.constraint_type = 'PRIMARY KEY' "
            "AND tc.table_schema = %s AND tc.table_name = %s",
            (self.schema, table),
        )
        return {r[0] for r in rows}

    def describe_table(self, table: str) -> TableInfo:
        specs = self._column_specs(table)
        if not specs:
            raise ValueError(f"table not found: {table}")
        pks = self._primary_keys(table)
        columns = [
            ColumnInfo(name=n, data_type=t, nullable=nl, primary_key=(n in pks))
            for (n, t, nl) in specs
        ]
        return TableInfo(name=table, columns=columns)

    def row_count(self, table: str) -> int:
        row = self._fetchone(f"SELECT COUNT(*) FROM {self._qualified(table)}")
        return int(row[0])

    def sample_values(self, table: str, column: str, *, limit: int = 5) -> list[Any]:
        # Plain LIMIT: first N rows, no DISTINCT/NOT NULL — stops immediately, no scan.
        rows = self._fetchall(
            f"SELECT {_ident(column)} FROM {self._qualified(table)} LIMIT %s",
            (limit,),
        )
        return [r[0] for r in rows]

    def is_unique(self, table: str, column: str) -> bool:
        row = self._fetchone(
            f"SELECT COUNT(*), COUNT(DISTINCT {_ident(column)}) FROM {self._qualified(table)}"
        )
        total, distinct = row
        # Non-null values are distinct and cover every row (no nulls). A PK qualifies.
        return total == distinct

    # -- execution --------------------------------------------------------- #

    def execute(self, sql: str, *, max_rows: int = 1000, timeout_s: float = 30.0) -> QueryResult:
        with self._conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (int(timeout_s * 1000),))
            cur.execute(sql)  # read-only connection -> writes raise
            columns = [d[0] for d in cur.description] if cur.description else []
            fetched = cur.fetchmany(max_rows + 1)
            truncated = len(fetched) > max_rows
            rows = [tuple(r) for r in fetched[:max_rows]]
        return QueryResult(columns=columns, rows=rows, row_count=len(rows), truncated=truncated)

    def explain(self, sql: str) -> str:
        rows = self._fetchall(f"EXPLAIN {sql}")
        return "\n".join(str(r[0]) for r in rows)

    def close(self) -> None:
        self._conn.close()
