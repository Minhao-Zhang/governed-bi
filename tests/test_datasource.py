"""DataSourceConfig + build_connector: the config-driven data source.

``[datasource]`` in governed_bi.toml selects which DB the engine/curator read;
``build_connector`` dials it. The Postgres/Redshift live paths need psycopg + a
server, so only routing and the SQLite path are exercised here.
"""

import pytest

from governed_bi.config import DataSourceConfig, Settings, load_settings
from governed_bi.gateway import build_connector
from governed_bi.gateway.connectors.sqlite import SqliteConnector


def test_default_datasource_is_sqlite_beer_factory():
    ds = Settings.for_env("dev").datasource
    assert ds.kind == "sqlite"
    assert ds.corpus_pin == "beer_factory"


def test_resolve_dsn_precedence(monkeypatch):
    monkeypatch.delenv("MY_DSN", raising=False)
    assert DataSourceConfig(dsn="host=x").resolve_dsn() == "host=x"  # inline wins
    assert DataSourceConfig(dsn_env="MY_DSN").resolve_dsn() is None  # env unset
    monkeypatch.setenv("MY_DSN", "host=y")
    assert DataSourceConfig(dsn_env="MY_DSN").resolve_dsn() == "host=y"  # from env
    assert DataSourceConfig(dsn="host=x", dsn_env="MY_DSN").resolve_dsn() == "host=x"  # inline beats env


def test_load_settings_parses_datasource(tmp_path):
    toml = tmp_path / "governed_bi.toml"
    toml.write_text(
        '[datasource]\n'
        'kind = "postgres"\n'
        'corpus_pin = "beer_factory"\n'
        'schema = "beer_factory"\n'
        'dsn_env = "PG_RENAME_DECOY_DSN"\n',
        encoding="utf-8",
    )
    ds = load_settings(toml).datasource
    assert ds.kind == "postgres"
    assert ds.schema == "beer_factory"
    assert ds.dsn_env == "PG_RENAME_DECOY_DSN"


def test_load_settings_ignores_unknown_datasource_keys(tmp_path):
    toml = tmp_path / "governed_bi.toml"
    toml.write_text('[datasource]\nkind = "sqlite"\nbogus = 1\n', encoding="utf-8")
    assert load_settings(toml).datasource.kind == "sqlite"


# --------------------------------------------------------------------------- #
# serving_schema (D15): the engine is uniformly schema-qualified
# --------------------------------------------------------------------------- #


def test_serving_schema_postgres_is_pinned_schema_or_none():
    # None spans every schema (bare refs fail closed); a pin scopes a single db_id.
    assert DataSourceConfig(kind="postgres", dsn="host=x").serving_schema() is None
    assert (
        DataSourceConfig(kind="postgres", dsn="host=x", schema="beer_factory").serving_schema()
        == "beer_factory"
    )
    assert DataSourceConfig(kind="redshift", schema="sales").serving_schema() == "sales"


def test_serving_schema_sqlite_defaults_to_corpus_pin():
    # SQLite has no native schema level, so the ATTACH alias (the fake schema) is
    # the pinned schema, else the corpus_pin.
    assert DataSourceConfig(kind="sqlite", corpus_pin="beer_factory").serving_schema() == "beer_factory"
    assert (
        DataSourceConfig(kind="sqlite", corpus_pin="beer_factory", schema="explicit").serving_schema()
        == "explicit"
    )


def test_build_connector_sqlite():
    conn = build_connector(DataSourceConfig(kind="sqlite", corpus_pin="beer_factory"))
    try:
        assert isinstance(conn, SqliteConnector)
        assert "customers" in conn.list_tables()
    finally:
        conn.close()


def test_build_connector_postgres_requires_dsn():
    with pytest.raises(ValueError, match="needs a DSN"):
        build_connector(DataSourceConfig(kind="postgres"))


def test_build_connector_unknown_kind():
    with pytest.raises(ValueError, match="unknown datasource kind"):
        build_connector(DataSourceConfig(kind="mysql"))
