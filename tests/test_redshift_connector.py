"""Offline unit tests for the Redshift connector.

No psycopg and no real cluster: everything runs against a hand-rolled fake
DBAPI-shaped connection/cursor injected via the ``connection=`` test seam that
``PostgresConnector.__init__`` accepts. Since ``RedshiftConnector`` is a thin
subclass that inherits nearly everything from ``PostgresConnector``, these
tests focus on what Redshift actually overrides -- ``list_tables`` and
``_column_specs`` querying the ``svv_*`` system views -- plus a couple of
smoke checks that inheritance (read-only enforcement, dialect, one plain
execution path) still works through the subclass.

NOTE: ``postgres.py`` is being implemented in parallel to the contract this
connector is built against (see the module docstring in
``src/governed_bi/gateway/connectors/redshift.py``). If it is still a stub
when this file is run, these tests will fail at construction time rather than
at the assertions they are meant to exercise -- see the test run notes in the
task summary.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.gateway.connectors.base import Dialect
from governed_bi.gateway.connectors.redshift import RedshiftConnector


class FakeCursor:
    """Minimal DBAPI-shaped cursor: records executed SQL, replays canned rows.

    ``canned`` maps a lowercase substring of the SQL to the rows that query
    should return; the first matching substring wins. Unmatched SQL returns no
    rows (empty result), which is the safe default for queries this test
    doesn't care about.
    """

    def __init__(self, canned: dict[str, list[tuple]], log: list[tuple[str, Any]]) -> None:
        self._canned = canned
        self._log = log
        self._rows: list[tuple] = []
        self.description: list[tuple] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self._log.append((sql, params))
        lowered = sql.lower()
        self._rows = []
        # Sentinel: any query parameterized with this "table name" mimics a
        # real DB returning no rows for a table that doesn't exist, regardless
        # of which system view the SQL text otherwise matches.
        if params and "does_not_exist" in params:
            self.description = []
            return
        for key, rows in self._canned.items():
            if key in lowered:
                self._rows = list(rows)
                break
        # DBAPI shape: description[i][0] is the column name. We don't have
        # real column names for canned data, so synthesize placeholders wide
        # enough to match the widest row -- good enough for callers that only
        # need `len(description)` or `[d[0] for d in description]`.
        width = len(self._rows[0]) if self._rows else 0
        self.description = [(f"col{i}",) for i in range(width)]

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None

    def fetchmany(self, size: int | None = None) -> list[tuple]:
        if size is None:
            return self.fetchall()
        rows, self._rows = self._rows[:size], self._rows[size:]
        return rows

    def close(self) -> None:
        pass

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.close()
        return False


class FakeConnection:
    """Minimal DBAPI-shaped connection: settable ``read_only``, records cursors."""

    def __init__(self, canned: dict[str, list[tuple]] | None = None) -> None:
        self._canned = canned or {}
        self.queries: list[tuple[str, Any]] = []
        self.read_only: bool | None = None
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._canned, self.queries)

    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

# Canned rows keyed by a lowercase substring of the SQL that should retrieve
# them. Kept broad (several plausible phrasings) since the exact SQL text of
# inherited PostgresConnector helpers (e.g. _primary_keys) is not fixed by
# this test file -- it lives in postgres.py.
CANNED: dict[str, list[tuple]] = {
    "svv_tables": [("customers",), ("orders",)],
    "svv_columns": [
        ("id", "integer", "NO", None),
        ("name", "character varying", "YES", 256),
    ],
    # Candidate phrasings for an information_schema-based primary-key lookup.
    "key_column_usage": [("id",)],
    "table_constraints": [("id",)],
    "primary key": [("id",)],
    "count(*)": [(3,)],
}


@pytest.fixture
def fake_conn() -> FakeConnection:
    return FakeConnection(CANNED)


@pytest.fixture
def redshift(fake_conn: FakeConnection) -> RedshiftConnector:
    return RedshiftConnector(
        "dsn-is-unused-with-injected-connection",
        schema="public",
        read_only=True,
        connection=fake_conn,
    )


# --------------------------------------------------------------------------- #
# dialect
# --------------------------------------------------------------------------- #


def test_dialect_is_redshift() -> None:
    assert RedshiftConnector.dialect is Dialect.redshift


# --------------------------------------------------------------------------- #
# list_tables() -> svv_tables
# --------------------------------------------------------------------------- #


def test_list_tables_queries_svv_tables(redshift: RedshiftConnector, fake_conn: FakeConnection) -> None:
    result = redshift.list_tables()

    assert result == ["customers", "orders"]
    assert fake_conn.queries, "expected list_tables() to issue a query"
    sql, params = fake_conn.queries[-1]
    assert "svv_tables" in sql.lower()
    assert params == ("public",)


# --------------------------------------------------------------------------- #
# describe_table() (inherited) driven by _column_specs() -> svv_columns
# --------------------------------------------------------------------------- #


def test_describe_table_queries_svv_columns_and_builds_columns(
    redshift: RedshiftConnector, fake_conn: FakeConnection
) -> None:
    table_info = redshift.describe_table("customers")

    # The svv_columns query happened with the right params.
    svv_columns_calls = [(sql, params) for sql, params in fake_conn.queries if "svv_columns" in sql.lower()]
    assert svv_columns_calls, "expected describe_table() to query svv_columns"
    sql, params = svv_columns_calls[-1]
    assert params == ("public", "customers")

    # ColumnInfo assembled correctly from the canned svv_columns rows.
    assert table_info.name == "customers"
    by_name = {c.name: c for c in table_info.columns}
    assert set(by_name) == {"id", "name"}

    id_col = by_name["id"]
    assert id_col.data_type == "integer"
    assert id_col.nullable is False

    name_col = by_name["name"]
    assert name_col.data_type == "character varying(256)"
    assert name_col.nullable is True

    # primary_key comes from _primary_keys() (inherited from PostgresConnector);
    # the canned data marks "id" as a primary key candidate.
    assert by_name["id"].primary_key is True
    assert by_name["name"].primary_key is False


def test_describe_table_missing_table_raises(redshift: RedshiftConnector) -> None:
    with pytest.raises(ValueError):
        redshift.describe_table("does_not_exist")


# --------------------------------------------------------------------------- #
# Inherited behavior still holds through the subclass
# --------------------------------------------------------------------------- #


def test_read_only_is_set_on_connection(fake_conn: FakeConnection) -> None:
    RedshiftConnector(
        "dsn-is-unused-with-injected-connection",
        schema="public",
        read_only=True,
        connection=fake_conn,
    )
    assert fake_conn.read_only is True


def test_read_only_false_does_not_set_flag(fake_conn: FakeConnection) -> None:
    RedshiftConnector(
        "dsn-is-unused-with-injected-connection",
        schema="public",
        read_only=False,
        connection=fake_conn,
    )
    assert fake_conn.read_only is not True


def test_row_count_smoke_through_subclass(redshift: RedshiftConnector, fake_conn: FakeConnection) -> None:
    """Light smoke that a plain inherited method (not overridden here) still
    works through the Redshift subclass against the fake connection."""
    count = redshift.row_count("orders")
    assert count == 3
    assert any("count(*)" in sql.lower() for sql, _ in fake_conn.queries)
