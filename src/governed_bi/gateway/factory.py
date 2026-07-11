"""Build a :class:`Connector` from a :class:`~governed_bi.config.DataSourceConfig`.

The config-driven data-source seam: a ``[datasource]`` table in ``governed_bi.toml``
(or CLI overrides) selects the engine, and this factory dials it. Drivers are
imported lazily, so importing this module never requires ``psycopg`` - only
opening a Postgres/Redshift connection does (install ``uv sync --extra postgres``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..config import _repo_root

if TYPE_CHECKING:
    from ..config import DataSourceConfig
    from .connectors.base import Connector


def build_connector(datasource: "DataSourceConfig") -> "Connector":
    """Construct the read-only :class:`Connector` a ``DataSourceConfig`` names.

    ``sqlite`` resolves ``sqlite_path`` against the repo root (like the corpus
    root). ``postgres`` / ``redshift`` need a DSN via ``resolve_dsn()``
    (``dsn_env`` preferred so the password stays out of git) and pass ``schema``
    through. Raises ``ValueError`` on a missing DSN or an unknown ``kind``.
    """
    kind = datasource.kind.lower()

    if kind == "sqlite":
        from .connectors.sqlite import SqliteConnector

        path = Path(datasource.sqlite_path)
        if not path.is_absolute():
            path = _repo_root() / path
        return SqliteConnector(path)

    if kind in ("postgres", "redshift"):
        dsn = datasource.resolve_dsn()
        if not dsn:
            raise ValueError(
                f"datasource kind={datasource.kind!r} needs a DSN: set [datasource].dsn_env "
                "to an env var holding the libpq DSN (e.g. PG_RENAME_DECOY_DSN), or dsn for a "
                "local secret-free one."
            )
        if kind == "postgres":
            from .connectors.postgres import PostgresConnector

            return PostgresConnector(dsn, schema=datasource.schema)
        from .connectors.redshift import RedshiftConnector

        return RedshiftConnector(dsn, schema=datasource.schema)

    raise ValueError(
        f"unknown datasource kind: {datasource.kind!r} (expected sqlite | postgres | redshift)"
    )
