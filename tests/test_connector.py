"""Tests for the DB connector, gateway, Facts profiling, and physical-existence.

Everything runs against a temporary SQLite database the test builds, so no real
BIRD data is required.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from governed_bi.corpus import load_corpus, validate_corpus, write_corpus
from governed_bi.corpus.schemas import Column, LogicalType, TableAsset
from governed_bi.curator.profile import profile_database
from governed_bi.gateway import Gateway, Identity, SqliteConnector


@pytest.fixture
def bird_db(tmp_path) -> Path:
    """A tiny two-table SQLite DB shaped like a BIRD DB (un-obfuscated)."""
    path = tmp_path / "demo.sqlite"
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE customers (CustomerID TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE orders (CustomerID TEXT, amount INTEGER, qty INTEGER);
        INSERT INTO customers VALUES ('01001', 'Alpha'), ('01002', 'Beta'), ('01003', 'Gamma');
        INSERT INTO orders VALUES ('01001', 10, 100), ('01001', 12, 110), ('01002', 5, 50);
        """
    )
    con.commit()
    con.close()
    return path


@pytest.fixture
def conn(bird_db) -> SqliteConnector:
    c = SqliteConnector(bird_db)  # read-only
    yield c
    c.close()


# --------------------------------------------------------------------------- #
# Connector: catalog introspection
# --------------------------------------------------------------------------- #


def test_list_and_describe(conn):
    assert conn.list_tables() == ["customers", "orders"]
    customers = conn.describe_table("customers")
    names = {c.name for c in customers.columns}
    assert names == {"CustomerID", "name"}
    cust = next(c for c in customers.columns if c.name == "CustomerID")
    assert cust.primary_key
    assert "TEXT" in cust.data_type.upper()


def test_row_count_samples_uniqueness(conn):
    assert conn.row_count("orders") == 3
    assert conn.is_unique("customers", "CustomerID")  # PK
    assert not conn.is_unique("orders", "CustomerID")  # repeats across orders
    samples = conn.sample_values("orders", "amount", limit=2)
    assert len(samples) <= 2


def test_describe_missing_table_raises(conn):
    with pytest.raises(ValueError):
        conn.describe_table("does_not_exist")


# --------------------------------------------------------------------------- #
# Connector: execution (read-only, row cap)
# --------------------------------------------------------------------------- #


def test_execute_select(conn):
    res = conn.execute("SELECT CustomerID, name FROM customers ORDER BY CustomerID")
    assert res.columns == ["CustomerID", "name"]
    assert res.row_count == 3
    assert not res.truncated


def test_execute_row_cap(conn):
    res = conn.execute("SELECT * FROM customers", max_rows=2)
    assert res.row_count == 2
    assert res.truncated


def test_execute_is_read_only(conn):
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO customers VALUES ('09999', 'Nope')")


def test_read_only_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        SqliteConnector(tmp_path / "absent.sqlite")


# --------------------------------------------------------------------------- #
# Gateway
# --------------------------------------------------------------------------- #


def test_gateway_executes_and_audits(conn):
    gw = Gateway(conn)
    res = gw.execute("SELECT COUNT(*) FROM orders", Identity(user="dev", all_access=True))
    assert res.rows[0][0] == 3
    assert len(gw.audit_log) == 1
    assert gw.audit_log[0].user == "dev"


# --------------------------------------------------------------------------- #
# Facts profiling
# --------------------------------------------------------------------------- #


def test_profile_database_emits_facts(conn):
    tables = profile_database(conn, db="demo")
    ids = {t.id for t in tables}
    assert ids == {"tbl_demo_customers", "tbl_demo_orders"}

    orders = next(t for t in tables if t.id == "tbl_demo_orders")
    assert orders.physical_name == "orders"
    assert orders.row_count is None  # bare-minimum: no COUNT(*) scan
    amount = next(c for c in orders.columns if c.physical_name == "amount")
    assert amount.logical_type == LogicalType.integer
    assert amount.description is None  # Inference tier left for the proposer

    customers = next(t for t in tables if t.id == "tbl_demo_customers")
    cust = next(c for c in customers.columns if c.physical_name == "CustomerID")
    assert cust.is_unique  # catalog-declared PK (no scan)
    assert cust.sample_values  # cheap LIMIT samples present


def test_profile_write_load_round_trip(conn, tmp_path):
    """Close the loop: profile a DB -> write the tree -> load it back -> validate."""
    assets = profile_database(conn, db="demo")
    write_corpus(tmp_path, "demo", assets)
    back = load_corpus(tmp_path, db="demo")
    assert {a.id for a in back.assets} == {a.id for a in assets}
    assert validate_corpus(back.assets) == []


# --------------------------------------------------------------------------- #
# Physical-existence validation (connector-backed)
# --------------------------------------------------------------------------- #


def test_physical_existence_green(conn):
    assets = profile_database(conn, db="demo")
    findings = validate_corpus(assets, connector=conn)
    assert findings == [], findings


def test_physical_existence_flags_missing_table_and_column(conn):
    ghost = TableAsset(id="tbl_demo_ghost", db="demo", physical_name="ghost")
    bad_col = TableAsset(
        id="tbl_demo_ordersx",
        db="demo",
        physical_name="orders",
        columns=[
            Column(
                physical_name="nope",
                physical_type="integer",
                logical_type=LogicalType.integer,
                nullable=True,
                is_unique=False,
            )
        ],
    )
    findings = validate_corpus([ghost, bad_col], connector=conn)
    codes = {f.code for f in findings}
    assert "missing-table" in codes
    assert "missing-column" in codes


# --------------------------------------------------------------------------- #
# Integration: the vendored BIRD database (skipped if it is not present)
# --------------------------------------------------------------------------- #

BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"


@pytest.mark.skipif(not BIRD_DB.exists(), reason="vendored beer_factory.sqlite not present")
def test_real_bird_db_end_to_end():
    conn = SqliteConnector(BIRD_DB)
    try:
        tables = conn.list_tables()
        assert {"customers", "transaction"} <= set(tables)
        assets = profile_database(conn, db="beer_factory")
        assert len(assets) == len(tables)
        # Assets profiled from a DB must pass physical-existence against that DB.
        assert validate_corpus(assets, connector=conn) == []
    finally:
        conn.close()
