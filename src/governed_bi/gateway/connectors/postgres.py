"""Postgres connector: read-only boundary over ``information_schema`` + psycopg.

Catalog introspection (``list_tables`` / ``describe_table`` / uniqueness / samples)
reads ``information_schema`` views, which is portable across Postgres-wire-protocol
engines — the ``RedshiftConnector`` subclass reuses this class and overrides only
the introspection seams (``list_tables``, ``_column_specs``, ``_primary_keys``)
with Redshift's ``svv_*`` views. Guarded execution (``execute``) sets a
per-statement ``statement_timeout``, best-effort injects a root ``LIMIT`` for
simple ``SELECT`` / ``UNION`` SQL that lacks one (so the server stops early
instead of buffering a huge result into libpq), then applies a client-side
``fetchmany`` row cap + truncation flag.

Read-only is security-critical: the gateway trusts this connector to make writes
fail, not the reverse. Connections open with ``autocommit=True`` (so session
``SET``s apply immediately), and when ``read_only=True`` the connector issues
``SET default_transaction_read_only = on`` so INSERT / UPDATE / DDL raise at
execute time even without an explicit transaction. It also sets
``connection.read_only = True``, which only affects ``BEGIN ... READ ONLY`` when
autocommit is off — useful for injected / pooled connections, but not the
enforcement path under autocommit. Both are belt-and-suspenders only —
production deployments MUST also connect through a read-only DB role / grant,
since an application bug or connector misuse should never be the last line of
defense.

``psycopg`` is imported lazily (see ``_require_psycopg``) so importing this
module — or constructing a ``PostgresConnector`` against an injected
``connection=`` (as the offline unit tests do) — never requires the driver to
be installed. Install the optional extra to open a real connection:

    uv sync
"""

from __future__ import annotations

from typing import Any

from .base import (
    ColumnInfo,
    Connector,
    Dialect,
    QueryResult,
    TableInfo,
    _force_row_limit,  # re-exported: shared by every connector
)


def _require_psycopg():
    try:
        import psycopg
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "PostgresConnector needs psycopg; it is a core dependency, so `uv sync` "
            "installs it"
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
            # Secondary under autocommit: only shapes BEGIN ... READ ONLY when a
            # transaction actually starts. Real write rejection is the SET below.
            self._conn.read_only = True
        # Session SETs for read-only (always when requested) and for real dial-outs
        # (scan determinism / search_path). Injected fakes see the read-only SET so
        # offline tests can assert it and simulate write rejection.
        if read_only or connection is None:
            with self._conn.cursor() as cur:
                if read_only:
                    # Under autocommit, psycopg's connection.read_only never reaches
                    # BEGIN ... READ ONLY. Session default is what makes writes fail.
                    cur.execute("SET default_transaction_read_only = on")
                if connection is None:
                    # Deterministic scan start. Postgres defaults ``synchronize_seqscans``
                    # to ON: a sequential scan may begin wherever another concurrent scan
                    # on the same table has reached, so an unordered ``LIMIT n`` returns a
                    # DIFFERENT n rows depending on what else is touching the table.
                    #
                    # ``profile_database`` samples column values with exactly that shape
                    # (``SELECT col FROM t LIMIT 5``, no ORDER BY — deliberately, because
                    # ordering would force a full scan on tables this size). Those samples
                    # go into the corpus, into the prompt, and therefore into the answer.
                    # Left on, two builds of the SAME arm can produce different corpora,
                    # and — worse — the arms of one run can differ from each other for a
                    # reason that has nothing to do with the intervention being measured.
                    # Observed live: the same schema profiled in two runs gave
                    # `2018/8/5` and `2018/8/1` for the same column.
                    #
                    # Turning it off costs nothing here (every scan starts at block 0) and
                    # buys reproducibility, which matters more the more builds run
                    # concurrently.
                    cur.execute("SET synchronize_seqscans = off")
                    # Pin unqualified names to the target schema (single-schema / BIRD
                    # eval). Skipped when ``schema`` is the default ``public`` or the
                    # caller spans all schemas (``schema=None`` in multi-schema mode).
                    if schema and schema != "public":
                        cur.execute(f"SET search_path TO {_ident(schema)}, public")

    def _qualified(self, table: str, schema: str | None = None) -> str:
        return f"{_ident(schema or self.schema)}.{_ident(table)}"

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

    def list_tables(self, schema: str | None = None) -> list[str]:
        rows = self._fetchall(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
            "ORDER BY table_name",
            (schema or self.schema,),
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

    def _column_specs(self, table: str, schema: str | None = None) -> list[tuple[str, str, bool]]:
        """(name, raw data type, nullable) per column. Seam: Redshift overrides
        this for ``svv_*`` views; keep the SQL here only, not inlined elsewhere.
        """
        rows = self._fetchall(
            "SELECT column_name, data_type, is_nullable, character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position",
            (schema or self.schema, table),
        )
        specs: list[tuple[str, str, bool]] = []
        for name, data_type, is_nullable, char_len in rows:
            raw_type = f"{data_type}({char_len})" if char_len is not None else data_type
            specs.append((name, raw_type, is_nullable == "YES"))
        return specs

    def _primary_keys(self, table: str, schema: str | None = None) -> set[str]:
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
            (schema or self.schema, table),
        )
        return {r[0] for r in rows}

    def describe_table(self, table: str, schema: str | None = None) -> TableInfo:
        specs = self._column_specs(table, schema)
        if not specs:
            raise ValueError(f"table not found: {table}")
        pks = self._primary_keys(table, schema)
        columns = [
            ColumnInfo(name=n, data_type=t, nullable=nl, primary_key=(n in pks))
            for (n, t, nl) in specs
        ]
        return TableInfo(name=table, columns=columns)

    def row_count(self, table: str, schema: str | None = None) -> int:
        row = self._fetchone(f"SELECT COUNT(*) FROM {self._qualified(table, schema)}")
        if row is None:  # an aggregate always returns a row; a None here is a driver fault
            raise RuntimeError(f"COUNT(*) returned no row for {self._qualified(table, schema)}")
        return int(row[0])

    def sample_values(
        self, table: str, column: str, *, limit: int = 5, schema: str | None = None
    ) -> list[Any]:
        # Plain LIMIT: first N rows, no DISTINCT/NOT NULL — stops immediately, no scan.
        rows = self._fetchall(
            f"SELECT {_ident(column)} FROM {self._qualified(table, schema)} LIMIT %s",
            (limit,),
        )
        return [r[0] for r in rows]

    def is_unique(self, table: str, column: str, schema: str | None = None) -> bool:
        row = self._fetchone(
            f"SELECT COUNT(*), COUNT(DISTINCT {_ident(column)}) "
            f"FROM {self._qualified(table, schema)}"
        )
        if row is None:  # as above: an aggregate with no row means the driver failed
            raise RuntimeError(f"uniqueness probe returned no row for {table}.{column}")
        total, distinct = row
        # Non-null values are distinct and cover every row (no nulls). A PK qualifies.
        return total == distinct

    # -- execution --------------------------------------------------------- #

    def execute(self, sql: str, *, max_rows: int = 1000, timeout_s: float = 30.0) -> QueryResult:
        # ``SET`` cannot take a bound parameter in Postgres (``$1`` is a
        # syntax error); interpolate a validated integer millisecond value.
        timeout_ms = int(timeout_s * 1000)
        if timeout_ms < 0:
            raise ValueError(f"timeout_s must be non-negative, got {timeout_s!r}")
        # Server-side stop for unbounded SELECT/UNION: inject LIMIT max_rows+1
        # so truncation detection still works, then fetchmany caps the client.
        limited_sql = _force_row_limit(sql, max_rows + 1, dialect="postgres")
        with self._conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {timeout_ms}")
            cur.execute(limited_sql)  # default_transaction_read_only -> writes raise
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
