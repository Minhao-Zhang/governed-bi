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
# multi_schema (D15): Postgres/Redshift span-all by default
# --------------------------------------------------------------------------- #


def test_multi_schema_defaults_on_for_postgres():
    ds = DataSourceConfig(kind="postgres", dsn="host=x")
    assert ds.multi_schema is True
    assert ds.is_multi_schema() is True


def test_is_multi_schema_true_for_postgres_redshift_unless_opted_out():
    assert DataSourceConfig(kind="postgres").is_multi_schema() is True
    assert DataSourceConfig(kind="redshift").is_multi_schema() is True
    assert DataSourceConfig(kind="postgres", multi_schema=False).is_multi_schema() is False


def test_sqlite_is_never_multi_schema_even_with_flag_and_no_schema():
    # SQLite runs schema=None but must stay single-schema; the flag is inert.
    ds = DataSourceConfig(kind="sqlite", schema=None, multi_schema=True)
    assert ds.is_multi_schema() is False


def test_load_settings_parses_multi_schema_opt_out(tmp_path):
    toml = tmp_path / "governed_bi.toml"
    toml.write_text(
        '[datasource]\n'
        'kind = "postgres"\n'
        'dsn_env = "PG_RENAME_DECOY_DSN"\n'
        'multi_schema = false\n'
        'schema = "beer_factory"\n',
        encoding="utf-8",
    )
    ds = load_settings(toml).datasource
    assert ds.multi_schema is False
    assert ds.is_multi_schema() is False
    assert ds.schema == "beer_factory"


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
