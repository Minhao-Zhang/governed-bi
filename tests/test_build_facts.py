"""``build_facts_corpus``: the layer-1 (no-AI) corpus generator.

Profiles the vendored beer_factory SQLite fixture into a facts-only corpus and
confirms the Inference tier is empty (no LLM ran) and the assets round-trip
through ``load_corpus``.
"""

from pathlib import Path

from governed_bi.config import DataSourceConfig, _repo_root
from governed_bi.corpus import load_corpus
from governed_bi.curator import build_facts_corpus
from governed_bi.curator.build import build_facts_all_schemas, main
from governed_bi.gateway.connectors.base import ColumnInfo, TableInfo
from governed_bi.gateway.connectors.sqlite import SqliteConnector

DB = "beer_factory"


def _sqlite_path() -> Path:
    return _repo_root() / "data" / "bird" / "beer_factory.sqlite"


def test_build_facts_writes_facts_only_corpus(tmp_path):
    conn = SqliteConnector(_sqlite_path())
    try:
        written = build_facts_corpus(conn, DB, tmp_path)
    finally:
        conn.close()

    assert written, "expected at least one asset file written"
    assert all(p.suffix == ".yaml" for p in written)
    assert (tmp_path / DB / "tables").is_dir()

    corpus = load_corpus(tmp_path, schema=DB)
    tables = corpus.assets
    assert len(tables) >= 2
    assert "customers" in {t.physical_name for t in tables}  # a known beer_factory table

    for t in tables:
        assert t.description is None  # Inference tier empty (no AI ran)
        assert t.row_count is None  # bare-minimum: no COUNT(*)
        assert t.columns, f"{t.physical_name} has no columns"
        for c in t.columns:
            assert c.physical_name and c.physical_type  # catalog facts present
            assert c.logical_type is not None
            assert c.description is None and c.role is None
    # PK-derived uniqueness (catalog, no scan) and cheap samples are present.
    customers = next(t for t in tables if t.physical_name == "customers")
    assert any(c.is_unique for c in customers.columns)  # at least the PK
    assert any(c.sample_values for c in customers.columns)  # LIMIT samples


def test_cli_main_writes(tmp_path):
    out = tmp_path / "corpus"
    rc = main(["--db", DB, "--sqlite", str(_sqlite_path()), "--out", str(out)])
    assert rc == 0
    assert list((out / DB / "tables").glob("*.yaml"))


class _FakeConn:
    """Minimal Connector double for the all-schemas iteration test: two schemas,
    each with one table of two columns."""

    def __init__(self, schema: str | None):
        self.schema = schema

    def list_schemas(self):
        return ["public", "s_one", "s_two"]

    def list_tables(self):
        return [] if self.schema == "public" else ["t"]

    def describe_table(self, table):
        return TableInfo(
            name=table,
            columns=[
                ColumnInfo(name="id", data_type="bigint", nullable=False, primary_key=True),
                ColumnInfo(name="label", data_type="text", nullable=True, primary_key=False),
            ],
        )

    def row_count(self, table):
        return 3

    def is_unique(self, table, column):
        return column == "id"

    def sample_values(self, table, column, *, limit=5):
        return ["a", "b"]

    def close(self):
        pass


def test_build_facts_all_schemas(tmp_path):
    ds = DataSourceConfig(kind="postgres", dsn="host=x")
    counts = build_facts_all_schemas(ds, tmp_path, connector_factory=lambda d: _FakeConn(d.schema))

    # Every schema is seen; empty ones (public) write nothing.
    assert counts == {"public": 0, "s_one": 1, "s_two": 1}
    assert (tmp_path / "s_one" / "tables" / "tbl_s_one_t.yaml").is_file()
    assert (tmp_path / "s_two" / "tables" / "tbl_s_two_t.yaml").is_file()
    assert not (tmp_path / "public").exists()

    # Each subtree is a valid facts-only corpus namespaced to its schema.
    corpus = load_corpus(tmp_path, schema="s_one")
    assert [t.physical_name for t in corpus.assets] == ["t"]
    assert corpus.assets[0].schema == "s_one"


def test_build_facts_all_schemas_multi_schema_pins_each_schema(tmp_path):
    # Regression: a multi_schema=True datasource must still profile each schema into
    # its OWN subtree, not collapse to "public". Mimic build_connector's schema
    # resolution so multi_schema => schema=None => Postgres coerces to "public";
    # each iteration must pin its schema (multi_schema=False) or every schema would
    # be profiled as public (list_tables() == [] there).
    def factory(d):
        effective = (None if d.is_multi_schema() else d.schema) or "public"
        return _FakeConn(effective)

    ds = DataSourceConfig(kind="postgres", dsn="host=x", multi_schema=True)
    counts = build_facts_all_schemas(ds, tmp_path, connector_factory=factory)

    assert counts == {"public": 0, "s_one": 1, "s_two": 1}
    assert (tmp_path / "s_one" / "tables" / "tbl_s_one_t.yaml").is_file()
    assert (tmp_path / "s_two" / "tables" / "tbl_s_two_t.yaml").is_file()
    assert not (tmp_path / "public").exists()


def test_build_facts_all_schemas_rejects_schemaless(tmp_path):
    import pytest

    ds = DataSourceConfig(kind="sqlite")
    conn = SqliteConnector(_sqlite_path())  # rejected by datasource.kind, not a missing method
    try:
        with pytest.raises(ValueError, match="no schemas to iterate"):
            build_facts_all_schemas(ds, tmp_path, connector_factory=lambda d: conn)
    finally:
        conn.close()


def test_enrich_table_partial_columns():
    """enrich_table backfills scanned facts for selected columns only."""
    from governed_bi.curator import enrich_table
    from governed_bi.curator.profile import profile_database

    conn = SqliteConnector(_sqlite_path())
    try:
        customers = next(t for t in profile_database(conn, DB) if t.physical_name == "customers")
        assert customers.row_count is None  # bare-minimum left it unset
        enriched = enrich_table(conn, customers, columns=["CustomerID"])
    finally:
        conn.close()

    assert enriched.row_count and enriched.row_count > 0  # COUNT(*) backfilled
    cid = next(c for c in enriched.columns if c.physical_name == "CustomerID")
    assert cid.is_unique is True  # scanned uniqueness
    # Non-selected columns pass through untouched (partial indexing).
    e_by_name = {c.physical_name: c for c in enriched.columns}
    for c in customers.columns:
        if c.physical_name != "CustomerID":
            assert e_by_name[c.physical_name] == c
    assert customers.row_count is None  # input not mutated


def test_enrich_table_rejects_unknown_column():
    import pytest

    from governed_bi.curator import enrich_table
    from governed_bi.curator.profile import profile_database

    conn = SqliteConnector(_sqlite_path())
    try:
        customers = next(t for t in profile_database(conn, DB) if t.physical_name == "customers")
        with pytest.raises(ValueError, match="not in"):
            enrich_table(conn, customers, columns=["does_not_exist"])
    finally:
        conn.close()
