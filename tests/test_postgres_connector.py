"""Offline unit tests for ``PostgresConnector``.

No real Postgres server and no ``psycopg`` install are required: every test
injects a fake DBAPI-shaped connection via the ``connection=`` constructor seam
(see ``postgres.py``'s docstring). ``FakeConnection``/``FakeCursor`` below mimic
just enough of the ``psycopg`` cursor protocol (context manager, ``execute``,
``fetchall``/``fetchone``/``fetchmany``, ``description``) to drive the connector
end to end and let tests assert on the exact SQL text and parameters it sends.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.gateway.connectors.postgres import PostgresConnector


class FakeCursor:
    """Records executed SQL and hands back canned rows keyed by query shape."""

    def __init__(self, conn: "FakeConnection") -> None:
        self._conn = conn
        self.description: list[tuple] | None = None
        self._rows: list[tuple] = []
        self._pos = 0

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self._conn.log.append((sql, params))
        self._pos = 0
        norm = " ".join(sql.split())

        def col(name: str) -> tuple:
            # DBAPI description entries are 7-tuples; only [0] (name) matters here.
            return (name, None, None, None, None, None, None)

        if "information_schema.schemata" in sql:
            self.description = [col("schema_name")]
            self._rows = [(name,) for name in self._conn.schemas_result]
        elif "information_schema.tables" in sql:
            self.description = [col("table_name")]
            self._rows = [(name,) for name in self._conn.tables_result]
        elif "information_schema.columns" in sql:
            table = params[1] if params else None
            self.description = [
                col("column_name"),
                col("data_type"),
                col("is_nullable"),
                col("character_maximum_length"),
            ]
            self._rows = list(self._conn.columns_result.get(table, []))
        elif "table_constraints" in sql:
            table = params[1] if params else None
            self.description = [col("column_name")]
            self._rows = [(c,) for c in self._conn.pk_result.get(table, [])]
        elif norm.startswith("SET statement_timeout"):
            self.description = None
            self._rows = []
        elif norm.startswith("EXPLAIN"):
            self.description = [col("QUERY PLAN")]
            self._rows = [(line,) for line in self._conn.explain_result]
        elif "COUNT(*)" in sql and "COUNT(DISTINCT" in sql:
            self.description = [col("count"), col("count_distinct")]
            self._rows = [self._conn.uniqueness_result]
        elif norm.startswith("SELECT COUNT(*) FROM"):
            self.description = [col("count")]
            self._rows = [(self._conn.row_count_result,)]
        elif norm.endswith("LIMIT %s"):  # plain sample_values query
            self.description = [col(self._conn.sample_column_name)]
            self._rows = [(v,) for v in self._conn.sample_values_result]
        else:
            # Generic path for execute()-style arbitrary SQL.
            self.description = self._conn.generic_description
            self._rows = list(self._conn.generic_rows)

    def fetchall(self) -> list[tuple]:
        rows = self._rows[self._pos :]
        self._pos = len(self._rows)
        return rows

    def fetchone(self) -> tuple | None:
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchmany(self, n: int) -> list[tuple]:
        rows = self._rows[self._pos : self._pos + n]
        self._pos += len(rows)
        return rows


class FakeConnection:
    """Minimal psycopg-shaped connection: settable ``read_only``, records SQL."""

    def __init__(self) -> None:
        self.read_only: Any = False
        self.log: list[tuple[str, tuple | None]] = []
        self.closed = False

        self.tables_result: list[str] = []
        self.schemas_result: list[str] = []
        self.columns_result: dict[str, list[tuple]] = {}
        self.pk_result: dict[str, list[str]] = {}
        self.row_count_result = 0
        self.uniqueness_result: tuple[int, int] = (0, 0)
        self.sample_column_name = "col"
        self.sample_values_result: list[Any] = []
        self.explain_result: list[str] = ["Seq Scan on t"]
        self.generic_description: list[tuple] | None = None
        self.generic_rows: list[tuple] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- #
# read_only wiring
# --------------------------------------------------------------------------- #


def test_read_only_true_sets_connection_flag() -> None:
    conn = FakeConnection()
    PostgresConnector("postgresql://x", connection=conn, read_only=True)
    assert conn.read_only is True


def test_read_only_false_does_not_force_flag() -> None:
    conn = FakeConnection()
    conn.read_only = "untouched"  # sentinel: connector must not overwrite it
    PostgresConnector("postgresql://x", connection=conn, read_only=False)
    assert conn.read_only == "untouched"


# --------------------------------------------------------------------------- #
# list_tables
# --------------------------------------------------------------------------- #


def test_list_tables_maps_rows_and_passes_schema_param() -> None:
    conn = FakeConnection()
    conn.tables_result = ["orders", "users"]
    pg = PostgresConnector("postgresql://x", connection=conn, schema="analytics")

    names = pg.list_tables()

    assert names == ["orders", "users"]
    sql, params = conn.log[-1]
    assert "information_schema.tables" in sql
    assert params == ("analytics",)


def test_list_tables_defaults_to_public_schema() -> None:
    conn = FakeConnection()
    pg = PostgresConnector("postgresql://x", connection=conn)
    pg.list_tables()
    _, params = conn.log[-1]
    assert params == ("public",)


def test_list_tables_explicit_schema_arg_targets_that_schema() -> None:
    # Explicit schema arg overrides the pinned schema for introspection (D15).
    conn = FakeConnection()
    pg = PostgresConnector("postgresql://x", connection=conn, schema="analytics")
    pg.list_tables(schema="beer_factory")
    _, params = conn.log[-1]
    assert params == ("beer_factory",)  # explicit arg wins over the pin


# --------------------------------------------------------------------------- #
# list_schemas
# --------------------------------------------------------------------------- #


def test_list_schemas_enumerates_user_schemas() -> None:
    conn = FakeConnection()
    conn.schemas_result = ["beer_factory", "public", "sales"]
    pg = PostgresConnector("postgresql://x", connection=conn)

    schemas = pg.list_schemas()

    assert schemas == ["beer_factory", "public", "sales"]
    sql, _ = conn.log[-1]
    assert "information_schema.schemata" in sql


# --------------------------------------------------------------------------- #
# schema-parameterized introspection: default == pinned (regression), explicit
# schema targets that schema
# --------------------------------------------------------------------------- #


def test_describe_table_default_schema_matches_pin_but_explicit_overrides() -> None:
    conn = FakeConnection()
    conn.columns_result["users"] = [("id", "integer", "NO", None)]
    conn.pk_result["users"] = ["id"]
    pg = PostgresConnector("postgresql://x", connection=conn, schema="analytics")

    # Default (no schema arg): uses the pinned schema in the column-specs query.
    pg.describe_table("users")
    col_calls = [(s, p) for s, p in conn.log if "information_schema.columns" in s]
    assert col_calls[-1][1] == ("analytics", "users")

    # Explicit schema arg targets that schema instead.
    pg.describe_table("users", schema="beer_factory")
    col_calls = [(s, p) for s, p in conn.log if "information_schema.columns" in s]
    assert col_calls[-1][1] == ("beer_factory", "users")


def test_row_count_sample_is_unique_honor_explicit_schema() -> None:
    conn = FakeConnection()
    conn.row_count_result = 7
    conn.sample_values_result = ["a"]
    conn.uniqueness_result = (7, 7)
    pg = PostgresConnector("postgresql://x", connection=conn, schema="public")

    # Default keeps today's behavior: pinned schema qualifies the table.
    pg.row_count("t")
    assert '"public"."t"' in conn.log[-1][0]

    # Explicit schema qualifies with that schema instead.
    pg.row_count("t", schema="beer_factory")
    assert '"beer_factory"."t"' in conn.log[-1][0]

    pg.sample_values("t", "c", limit=1, schema="beer_factory")
    assert '"beer_factory"."t"' in conn.log[-1][0]

    pg.is_unique("t", "c", schema="beer_factory")
    assert '"beer_factory"."t"' in conn.log[-1][0]


# --------------------------------------------------------------------------- #
# describe_table
# --------------------------------------------------------------------------- #


def test_describe_table_builds_columns_with_primary_key_and_length() -> None:
    conn = FakeConnection()
    conn.columns_result["users"] = [
        ("id", "integer", "NO", None),
        ("email", "character varying", "YES", 255),
    ]
    conn.pk_result["users"] = ["id"]
    pg = PostgresConnector("postgresql://x", connection=conn)

    info = pg.describe_table("users")

    assert info.name == "users"
    by_name = {c.name: c for c in info.columns}
    assert by_name["id"].data_type == "integer"
    assert by_name["id"].nullable is False
    assert by_name["id"].primary_key is True
    assert by_name["email"].data_type == "character varying(255)"
    assert by_name["email"].nullable is True
    assert by_name["email"].primary_key is False


def test_describe_table_missing_raises_value_error() -> None:
    conn = FakeConnection()  # no columns_result entries -> empty specs
    pg = PostgresConnector("postgresql://x", connection=conn)
    with pytest.raises(ValueError):
        pg.describe_table("ghost")


# --------------------------------------------------------------------------- #
# row_count / sample_values / is_unique
# --------------------------------------------------------------------------- #


def test_row_count_returns_int_and_quotes_schema_qualified_table() -> None:
    conn = FakeConnection()
    conn.row_count_result = 42
    pg = PostgresConnector("postgresql://x", connection=conn, schema="public")

    assert pg.row_count("users") == 42
    sql, _ = conn.log[-1]
    assert '"public"."users"' in sql


def test_sample_values_returns_values_and_passes_limit_param() -> None:
    conn = FakeConnection()
    conn.sample_column_name = "email"
    conn.sample_values_result = ["a@x.com", "b@x.com"]
    pg = PostgresConnector("postgresql://x", connection=conn, schema="public")

    values = pg.sample_values("users", "email", limit=2)

    assert values == ["a@x.com", "b@x.com"]
    sql, params = conn.log[-1]
    assert '"public"."users"' in sql
    assert '"email"' in sql
    assert params == (2,)


def test_is_unique_true_and_false() -> None:
    conn = FakeConnection()
    pg = PostgresConnector("postgresql://x", connection=conn, schema="public")

    conn.uniqueness_result = (10, 10)
    assert pg.is_unique("users", "email") is True
    sql, _ = conn.log[-1]
    assert '"public"."users"' in sql
    assert '"email"' in sql

    conn.uniqueness_result = (10, 7)
    assert pg.is_unique("users", "email") is False


# --------------------------------------------------------------------------- #
# execute(): statement_timeout, row cap, column mapping
# --------------------------------------------------------------------------- #


def test_execute_sets_statement_timeout_in_milliseconds() -> None:
    conn = FakeConnection()
    conn.generic_description = [("id", None, None, None, None, None, None)]
    conn.generic_rows = [(1,)]
    pg = PostgresConnector("postgresql://x", connection=conn)

    pg.execute("SELECT id FROM users", timeout_s=2.5)

    timeout_sqls = [sql for sql, _params in conn.log if "statement_timeout" in sql]
    assert timeout_sqls == ["SET statement_timeout = 2500"]


def test_execute_applies_row_cap_and_marks_truncated() -> None:
    conn = FakeConnection()
    conn.generic_description = [
        ("id", None, None, None, None, None, None),
        ("name", None, None, None, None, None, None),
    ]
    conn.generic_rows = [(i, f"row{i}") for i in range(5)]
    pg = PostgresConnector("postgresql://x", connection=conn)

    result = pg.execute("SELECT * FROM users", max_rows=3)

    assert result.columns == ["id", "name"]
    assert result.rows == [(0, "row0"), (1, "row1"), (2, "row2")]
    assert result.row_count == 3
    assert result.truncated is True


def test_execute_not_truncated_when_within_row_cap() -> None:
    conn = FakeConnection()
    conn.generic_description = [("id", None, None, None, None, None, None)]
    conn.generic_rows = [(1,), (2,)]
    pg = PostgresConnector("postgresql://x", connection=conn)

    result = pg.execute("SELECT id FROM users", max_rows=10)

    assert result.row_count == 2
    assert result.truncated is False


def test_execute_no_description_when_no_result_columns() -> None:
    conn = FakeConnection()
    conn.generic_description = None
    conn.generic_rows = []
    pg = PostgresConnector("postgresql://x", connection=conn)

    result = pg.execute("SELECT 1 WHERE FALSE")

    assert result.columns == []
    assert result.rows == []


# --------------------------------------------------------------------------- #
# explain() and close()
# --------------------------------------------------------------------------- #


def test_explain_joins_plan_lines() -> None:
    conn = FakeConnection()
    conn.explain_result = ["Seq Scan on users", "  Filter: (id = 1)"]
    pg = PostgresConnector("postgresql://x", connection=conn)

    plan = pg.explain("SELECT * FROM users WHERE id = 1")

    assert plan == "Seq Scan on users\n  Filter: (id = 1)"
    sql, _ = conn.log[-1]
    assert sql.startswith("EXPLAIN SELECT")


def test_close_closes_underlying_connection() -> None:
    conn = FakeConnection()
    pg = PostgresConnector("postgresql://x", connection=conn)
    pg.close()
    assert conn.closed is True


# --------------------------------------------------------------------------- #
# Lazy psycopg import
# --------------------------------------------------------------------------- #


def test_missing_psycopg_raises_import_error_with_install_hint(monkeypatch) -> None:
    import governed_bi.gateway.connectors.postgres as postgres_mod

    def _fake_require_psycopg() -> None:
        raise ImportError(
            "PostgresConnector needs psycopg; install it: uv sync --extra postgres"
        )

    monkeypatch.setattr(postgres_mod, "_require_psycopg", _fake_require_psycopg)

    with pytest.raises(ImportError, match="uv sync --extra postgres"):
        postgres_mod.PostgresConnector("postgresql://localhost/db")


def test_connection_seam_avoids_psycopg_import_entirely(monkeypatch) -> None:
    """Constructing with connection= must never call the lazy import at all."""
    import governed_bi.gateway.connectors.postgres as postgres_mod

    def _boom() -> None:
        raise AssertionError("_require_psycopg should not be called when connection= is given")

    monkeypatch.setattr(postgres_mod, "_require_psycopg", _boom)

    conn = FakeConnection()
    pg = postgres_mod.PostgresConnector("postgresql://x", connection=conn)
    assert pg.list_tables() == []


# --------------------------------------------------------------------------- #
# LIVE integration: pg_rename_decoy (D15 multi-schema span)
#
# Opt-in and self-skipping so the hermetic offline suite stays green: runs only
# when PG_RENAME_DECOY_DSN is set, psycopg is importable, AND the connection
# opens. Verifies that one connector spans every user schema and can introspect
# a specific one (beer_factory) across the schema boundary.
# --------------------------------------------------------------------------- #

import os  # noqa: E402


def _live_pg_connector():
    """Open a real PostgresConnector against PG_RENAME_DECOY_DSN, or skip.

    Skips (never fails) when the env var is absent, psycopg is not installed, or
    the connection cannot be opened - keeping the offline suite deterministic.
    """
    dsn = os.environ.get("PG_RENAME_DECOY_DSN")
    if not dsn:
        pytest.skip("PG_RENAME_DECOY_DSN not set; skipping live Postgres integration test")
    try:
        import psycopg  # noqa: F401
    except ImportError:
        pytest.skip("psycopg not installed (uv sync --extra postgres); skipping live test")
    try:
        # schema unpinned -> span-all-capable, exactly as the factory builds it
        # for a datasource with no pinned schema.
        return PostgresConnector(dsn, schema=None)
    except Exception as e:  # pragma: no cover - environment-dependent
        pytest.skip(f"could not connect to PG_RENAME_DECOY_DSN: {e}")


def test_live_pg_rename_decoy_spans_schemas_and_introspects_beer_factory() -> None:
    pg = _live_pg_connector()
    try:
        schemas = pg.list_schemas()
        # One connection sees many user schemas, including the beer_factory db_id.
        assert len(schemas) > 1, f"expected multiple user schemas, got {schemas!r}"
        assert "beer_factory" in schemas, f"beer_factory not among schemas: {schemas!r}"

        # Introspecting a specific schema across the boundary yields its tables.
        tables = pg.list_tables(schema="beer_factory")
        assert tables, "expected beer_factory to expose at least one table"
        # And a table can be described within that schema.
        info = pg.describe_table(tables[0], schema="beer_factory")
        assert info.columns, f"expected columns for {tables[0]!r} in beer_factory"
    finally:
        pg.close()
