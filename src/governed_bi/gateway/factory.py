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


# libpq connect_timeout (seconds). Keep dials short so a down Postgres fails fast
# at startup / first chat instead of hanging ~2 minutes on the OS TCP timeout.
_DEFAULT_CONNECT_TIMEOUT_S = 5


def build_connector(
    datasource: "DataSourceConfig",
    *,
    connect_timeout: float | None = _DEFAULT_CONNECT_TIMEOUT_S,
) -> "Connector":
    """Construct the read-only :class:`Connector` a ``DataSourceConfig`` names.

    ``sqlite`` resolves ``sqlite_path`` against the repo root (like the corpus
    root). ``postgres`` / ``redshift`` need a DSN via ``resolve_dsn()``
    (``dsn_env`` preferred so the password stays out of git) and pass ``schema``
    through. Raises ``ValueError`` on a missing DSN or an unknown ``kind``.

    ``connect_timeout`` is forwarded to psycopg as libpq ``connect_timeout``
    (ignored for SQLite). Pass ``None`` to use the driver/OS default.
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
        # Multi-schema (D15): span-all by default for postgres/redshift.
        # schema=None lets the connector enumerate every schema via list_schemas()
        # and introspect any of them via the explicit ``schema=`` argument. NB the
        # connector still DEFAULTS unqualified introspection to "public", so a
        # multi-schema caller must pass an explicit ``schema=`` per call (see
        # build_facts_all_schemas, which pins each schema). Opt out with
        # multi_schema=False to pin datasource.schema.
        schema = None if datasource.is_multi_schema() else datasource.schema
        connect_kwargs: dict = {}
        if connect_timeout is not None:
            # libpq wants an integer second count.
            connect_kwargs["connect_timeout"] = max(1, int(connect_timeout))
        if kind == "postgres":
            from .connectors.postgres import PostgresConnector

            return PostgresConnector(dsn, schema=schema, **connect_kwargs)
        from .connectors.redshift import RedshiftConnector

        return RedshiftConnector(dsn, schema=schema, **connect_kwargs)

    raise ValueError(
        f"unknown datasource kind: {datasource.kind!r} (expected sqlite | postgres | redshift)"
    )
