"""Postgres faults are classified by SQLSTATE class, never by the message.

Class ``42`` is a query fault. Classes ``08``, ``53`` and ``57`` are infrastructure.
A skip prints its reason: a silent skip looks like a pass.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


#: Class ``42`` statements. A prose matcher filed these as infrastructure.
QUERY_FAULTS: tuple[tuple[str, str, str], ...] = (
    ("42P01", "undefined_table", "SELECT 1 FROM no_such_table_xyz"),
    ("42703", "undefined_column", "SELECT no_such_column_xyz FROM {t}"),
    ("42601", "syntax_error", "SELEKT 1"),
    ("42883", "undefined_function", "SELECT no_such_fn_xyz(1)"),
    ("42883", "wrong_arity", "SELECT abs(1, 2)"),
)


def _dsn() -> str:
    """DSN from the environment or ``.env``. Never logged: it carries the credential."""
    dsn = os.environ.get("GOVERNED_BI_PG_DSN")
    if dsn:
        return dsn
    env = pathlib.Path(__file__).resolve().parent.parent.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "GOVERNED_BI_PG_DSN":
                return value.strip().strip("\"'")
    return ""


@pytest.fixture(scope="module")
def dsn() -> str:
    """Skip with a printed reason when no server answers."""
    value = _dsn()
    if not value:
        pytest.skip(
            "no Postgres DSN: set GOVERNED_BI_PG_DSN. "
            "Parcel C is Postgres-only (decision #40) and a connector cannot be "
            "tested without a server; these tests are not optional, they are pending."
        )
    import psycopg

    try:
        with psycopg.connect(value, connect_timeout=5):
            pass
    except Exception as err:  # pragma: no cover - environment dependent
        pytest.skip(f"Postgres unreachable ({type(err).__name__}); see the DSN's host")
    return value


@pytest.fixture
def fixture_schema(dsn):
    """Throwaway schema, dropped afterwards, so the probe does not write the eval corpus.
    """
    import psycopg

    name = "gbi_contract_probe"
    with psycopg.connect(dsn, autocommit=True) as con:
        con.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        con.execute(f'CREATE SCHEMA "{name}"')
        con.execute(f'CREATE TABLE "{name}".customers (id integer PRIMARY KEY, email text)')
        con.execute(
            f'CREATE TABLE "{name}".orders (id integer PRIMARY KEY, '
            f'cid integer REFERENCES "{name}".customers(id), amount numeric)'
        )
        con.execute(f"INSERT INTO \"{name}\".customers VALUES (1,'a@x'),(2,'b@x'),(3,'c@x')")
    try:
        yield name
    finally:
        with psycopg.connect(dsn, autocommit=True) as con:
            con.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


@pytest.fixture
def connector(dsn, fixture_schema):
    from governed_bi.datasource.postgres import PostgresConnector  # type: ignore[import-not-found]

    return PostgresConnector(dsn)


def test_execute_returns_columns_rows_and_truncated(connector, fixture_schema) -> None:
    """A stub raising ``ConnectionError`` for an unwritten method still left the suite green."""
    columns, rows, truncated = connector.execute(
        f'SELECT id, email FROM "{fixture_schema}".customers ORDER BY id'
    )
    assert list(columns) == ["id", "email"]
    assert len(rows) == 3
    assert truncated is False


def test_truncated_comes_from_a_max_rows_plus_one_fetch(connector, fixture_schema) -> None:
    """Exactly ``max_rows`` rows is not truncation; the fetch asks for one more so the two differ."""
    sql = f'SELECT id FROM "{fixture_schema}".customers ORDER BY id'
    assert connector.execute(sql, max_rows=2)[2] is True
    assert connector.execute(sql, max_rows=3)[2] is False, "exactly at the cap is not truncated"
    assert connector.execute(sql, max_rows=4)[2] is False


@pytest.mark.parametrize("sqlstate,label,sql", QUERY_FAULTS, ids=[q[1] for q in QUERY_FAULTS])
def test_a_class_42_sqlstate_is_a_query_fault(connector, fixture_schema, sqlstate, label, sql) -> None:
    """The error carries its SQLSTATE, so classification does not parse the message."""
    from governed_bi.datasource import errors  # type: ignore[import-not-found]

    with pytest.raises(errors.QueryError) as caught:
        connector.execute(sql.format(t=f'"{fixture_schema}".customers'))
    assert getattr(caught.value, "sqlstate", None) == sqlstate, (
        f"{label}: the error must carry its SQLSTATE, so a reader can classify it "
        "without parsing prose"
    )


def test_statement_timeout_cancels_one_statement_and_keeps_the_connection(dsn) -> None:
    """``57014`` is a query fault and the handle stays open; class 57 would discard it."""
    from governed_bi.datasource import errors  # type: ignore[import-not-found]
    from governed_bi.datasource.postgres import PostgresConnector  # type: ignore[import-not-found]

    connector = PostgresConnector(dsn, statement_timeout_ms=50)
    with pytest.raises(errors.QueryError) as caught:
        connector.execute("SELECT pg_sleep(5)")
    assert getattr(caught.value, "sqlstate", None) == "57014", (
        "the cancellation must carry its SQLSTATE, or a reader cannot tell a timeout from "
        "any other statement fault without parsing prose"
    )
    assert connector._conn is not None, (
        "statement_timeout discarded a working connection: 57014 fell through to the "
        "class-57 branch, which is for the operator taking the server away"
    )
    assert connector.execute("SELECT 1")[1] == [(1,)], (
        "the same connector must still serve a query that fits inside the bound"
    )


def test_the_statement_timeout_default_is_the_registers_and_not_a_literal() -> None:
    """``None`` takes the declared default. Zero stays zero, which is how Postgres spells no bound."""
    from governed_bi.datasource.postgres import PostgresConnector  # type: ignore[import-not-found]
    from governed_bi.registry.settings import setting_default

    assert PostgresConnector("postgresql://unused")._statement_timeout_ms == int(
        setting_default("statement_timeout_ms")
    )
    assert PostgresConnector("postgresql://unused", statement_timeout_ms=0)._statement_timeout_ms == 0, (
        "0 must stay 0: Postgres already spells 'no bound' that way, and a falsy check here "
        "would silently restore the register's value"
    )


def test_a_class_08_sqlstate_is_infrastructure(dsn) -> None:
    """The error carries a class ``08`` code, so always raising ``ConnectionError`` does not pass."""
    from governed_bi.datasource import errors  # type: ignore[import-not-found]
    from governed_bi.datasource.postgres import PostgresConnector  # type: ignore[import-not-found]

    unreachable = PostgresConnector("postgresql://nobody@127.0.0.1:1/none")
    with pytest.raises(errors.ConnectionError) as caught:
        unreachable.execute("SELECT 1")

    state = getattr(caught.value, "sqlstate", None)
    assert state and str(state).startswith("08"), (
        f"expected a class-08 SQLSTATE on the error, got {state!r}. Without it, this "
        "test is satisfied by a connector that raises ConnectionError for everything, "
        "including for query faults."
    )


def test_classification_reads_the_code_not_the_message(connector, fixture_schema) -> None:
    """A query fault whose message contains "connection" is still a query fault."""
    from governed_bi.datasource import errors  # type: ignore[import-not-found]

    with pytest.raises(errors.QueryError):
        connector.execute("SELECT connection_is_not_a_function_xyz(1)")


def test_introspection_classifies_its_own_failures(dsn) -> None:
    """Introspection raises ``QueryError`` or ``ConnectionError``, not a raw driver exception."""
    from governed_bi.datasource import errors  # type: ignore[import-not-found]
    from governed_bi.datasource.postgres import PostgresConnector  # type: ignore[import-not-found]

    unreachable = PostgresConnector("postgresql://nobody@127.0.0.1:1/none")
    with pytest.raises((errors.ConnectionError, errors.QueryError)):
        unreachable.introspect("public")


def _connector_with(conn: object) -> object:
    """Fake handle, reconnects counted. These tests need a failure on demand, not a server."""
    from governed_bi.datasource.postgres import (  # type: ignore[import-not-found]
        PostgresConnector,
    )

    class _Fake(PostgresConnector):
        def __init__(self) -> None:
            self._dsn = "postgresql://unused"
            self._max_rows = 10
            self._statement_timeout_ms = 0
            self._conn = None
            self.connects = 0

        def _connect(self):  # noqa: ANN202
            if self._conn is None:
                self.connects += 1
                self._conn = conn
            return self._conn

    return _Fake()


def test_one_connection_blip_does_not_poison_the_rest_of_the_run() -> None:
    """A closed connection from psycopg has ``sqlstate=None`` and must be ``ConnectionError``, not ``QueryError``.
    """
    import psycopg

    from governed_bi.datasource import errors  # type: ignore[import-not-found]

    class _Dead:
        closed = False
        broken = False

        def execute(self, sql, *args, **kwargs):  # noqa: ANN001, ANN202
            raise psycopg.OperationalError("the connection is closed")

        def close(self) -> None:
            pass

    connector = _connector_with(_Dead())
    for _ in range(3):
        with pytest.raises(errors.ConnectionError) as caught:
            connector.execute("SELECT 1")
        assert caught.value.sqlstate, "an infrastructure fault must carry a class-08 code"
        assert connector._conn is None, "the dead handle survived the statement that killed it"

    assert connector.connects == 3, (
        f"reconnected {connector.connects} time(s) in three calls; the cached handle is still "
        "being reused after a failure"
    )


def test_a_client_side_fault_that_is_not_the_connection_is_still_a_query_fault() -> None:
    """A ``ProgrammingError`` with no SQLSTATE stays a query fault even when the message says "connection".
    """
    import psycopg

    from governed_bi.datasource import errors  # type: ignore[import-not-found]

    class _Fussy:
        closed = False
        broken = False

        def execute(self, sql, *args, **kwargs):  # noqa: ANN001, ANN202
            # No SQLSTATE: psycopg raises this client-side, before the server sees the statement.
            raise psycopg.ProgrammingError("the connection cannot be used in this context")

        def close(self) -> None:
            pass

    connector = _connector_with(_Fussy())
    with pytest.raises(errors.QueryError):
        connector.execute("SELECT 1")
    assert connector._conn is not None, (
        "a statement fault must not discard a working connection — and the message here "
        "contains the word 'connection', so a prose-matching classifier inverts this"
    )


def test_the_sqlite_adapter_classifies_by_code_and_not_by_prose(tmp_path) -> None:
    """``SQLITE_READONLY`` and "no such function" are query faults, even if the message says "connection".
    """
    import sqlite3

    from governed_bi.datasource import errors  # type: ignore[import-not-found]
    from governed_bi.datasource.sqlite import SqliteConnector  # type: ignore[import-not-found]

    path = tmp_path / "taxonomy.db"
    seed = sqlite3.connect(path)
    seed.execute("CREATE TABLE t (a INTEGER)")
    seed.commit()
    seed.close()

    connector = SqliteConnector(path)
    statement_faults = [
        "SELECT connection_is_not_a_function_xyz(1)",
        "SELECT nope FROM t",
        "SELEC 1",
        "SELECT * FROM no_such_table",
        "INSERT INTO t VALUES (1)",
    ]
    for sql in statement_faults:
        with pytest.raises(errors.QueryError, match=r".*") as caught:
            connector.execute(sql)
        assert not isinstance(caught.value, errors.ConnectionError), sql

    # A missing file is still infrastructure.
    missing = SqliteConnector(tmp_path / "not-here.db")
    with pytest.raises(errors.ConnectionError):
        missing.execute("SELECT 1")


def test_a_statement_govern_permits_is_one_the_connector_executes(connector, fixture_schema) -> None:
    """Qualified ``schema.table`` is what ``govern`` accepts, and Postgres runs that form.
    """
    from governed_bi.corpus.analyst import analyst_corpus_from_keys  # type: ignore[import-not-found]
    from governed_bi.govern.check import check  # type: ignore[import-not-found]

    sql = f'SELECT count(*) FROM "{fixture_schema}".customers'
    result = check(
        f"SELECT count(*) FROM {fixture_schema}.customers",
        retrieved_tables=frozenset({f"{fixture_schema}.customers"}),
        corpus=analyst_corpus_from_keys(allowed=(f"{fixture_schema}.customers.id",)),
    )
    assert result["passed"] is True, result
    columns, rows, _ = connector.execute(sql)
    assert rows[0][0] == 3, "govern permitted it; the connector must be able to run it"


def test_every_seeded_asset_has_a_non_empty_summary(connector, fixture_schema) -> None:
    """An empty summary would look like a retrieval miss."""
    from governed_bi.corpus.seed import seed  # type: ignore[import-not-found]

    assets, problems = seed(connector.introspect(fixture_schema), schema=fixture_schema)
    assert assets, problems
    assert all(a.summary and a.summary.strip() for a in assets)


def test_the_seed_covers_every_table_and_column(connector, fixture_schema) -> None:
    from governed_bi.corpus.seed import seed  # type: ignore[import-not-found]

    assets, _ = seed(connector.introspect(fixture_schema), schema=fixture_schema)
    kinds: dict[str, int] = {}
    for a in assets:
        kinds[a.asset_type] = kinds.get(a.asset_type, 0) + 1
    assert kinds.get("table") == 2
    assert kinds.get("column") == 5  # 2 customers + 3 orders


def test_the_seed_makes_no_model_call(connector, fixture_schema, monkeypatch) -> None:
    """The seed opens no socket; introspection is already done by the fixture."""
    import socket

    intro = connector.introspect(fixture_schema)

    def refuse(*args, **kwargs):  # pragma: no cover - the point is that it is not hit
        raise AssertionError("the seed opened a socket; it must be model-free")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    from governed_bi.corpus.seed import seed  # type: ignore[import-not-found]

    assets, _ = seed(intro, schema=fixture_schema)
    assert assets


def test_the_seed_is_deterministic(connector, fixture_schema) -> None:
    """A nondeterministic seed would change ``corpus_content_hash`` and make two runs look different."""
    from governed_bi.corpus.seed import seed  # type: ignore[import-not-found]

    intro = connector.introspect(fixture_schema)
    first, _ = seed(intro, schema=fixture_schema)
    second, _ = seed(intro, schema=fixture_schema)
    assert [a.id for a in first] == [a.id for a in second]
    assert [a.summary for a in first] == [a.summary for a in second]


def test_seeded_assets_pass_corpus_validation(connector, fixture_schema) -> None:
    """Seed output has to pass corpus validation, which neither half checks alone."""
    from governed_bi.corpus.seed import seed  # type: ignore[import-not-found]
    from governed_bi.corpus.validate import problems_with  # type: ignore[import-not-found]

    assets, _ = seed(connector.introspect(fixture_schema), schema=fixture_schema)
    assert not [p for a in assets for p in problems_with(a)]
