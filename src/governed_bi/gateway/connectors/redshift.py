"""Redshift connector: reuses ``PostgresConnector``'s wire protocol, execution,
and read-only enforcement; overrides only catalog introspection.

Amazon Redshift speaks the Postgres wire protocol (its engine forked Postgres
8.0.2), so ``psycopg`` connects to a Redshift cluster exactly as it would to a
real Postgres server. This connector therefore *subclasses*
:class:`~governed_bi.gateway.connectors.postgres.PostgresConnector` and
inherits its connection handling, read-only enforcement
(``self._conn.read_only = True``), ``execute()``, ``row_count()``,
``sample_values()``, ``is_unique()``, ``explain()``, and ``close()`` unchanged
-- those are standard SQL / standard psycopg usage that Redshift supports.

It overrides only the two catalog-introspection seams that Redshift resolves
differently, because Redshift does not populate ``information_schema`` the
way Postgres does; it instead exposes ``svv_*`` ("system view, visible")
views that are schema/permission-aware:

* :meth:`list_tables` -> ``svv_tables``
* :meth:`_column_specs` -> ``svv_columns``

``_primary_keys`` is **inherited unchanged** from ``PostgresConnector``
(``information_schema``-based): Redshift does accept and report ``PRIMARY
KEY`` constraints via ``information_schema``, but -- unlike Postgres -- never
enforces them on write. They are declared/informational only, not a real
integrity guarantee, but still worth surfacing to the catalog as a signal, so
no override is needed here.

Caveats (read before pointing this at a real cluster):

* Redshift's engine is based on a very old Postgres fork. Basic read-only
  query execution over the wire protocol works, but some psycopg3 features --
  e.g. pipeline mode, server-side (named) cursors, and some newer type OIDs --
  are either unsupported or behave differently on Redshift. Nothing this
  connector relies on depends on those features; it uses plain
  ``execute`` / ``fetchall`` on ordinary (client-side) cursors.
* ``SET statement_timeout`` is honored by Redshift, but cluster-side Workload
  Management (WLM) queue configuration also applies and can queue or kill a
  query independently of this timeout.
* Read-only is enforced the same way as for Postgres: the parent sets
  ``self._conn.read_only = True`` on the psycopg connection. In production
  this client-side flag should be paired with a read-only IAM role / DB user
  grant on the cluster itself -- the flag alone is a courtesy, not a security
  boundary.
* **Untested against a live Redshift cluster.** There is no AWS access
  available while writing this connector. The implementation is best-effort
  against the documented ``svv_tables`` / ``svv_columns`` schemas and the
  assumption that psycopg's standard wire-protocol usage behaves like
  Postgres. Treat this as needing a real integration smoke test against an
  actual cluster before production use.
"""

from __future__ import annotations

from .base import Dialect
from .postgres import PostgresConnector


class RedshiftConnector(PostgresConnector):
    """Redshift catalog introspection layered on the Postgres wire protocol.

    Everything except catalog introspection (table/column listing) is
    inherited from :class:`PostgresConnector` unchanged. See the module
    docstring for the ``svv_*`` views this overrides and the caveats around
    running this against a real cluster.
    """

    dialect = Dialect.redshift

    # ``__init__`` is intentionally not overridden: the parent's signature
    # (dsn, schema, read_only, connection, **connect_kwargs) already covers
    # everything Redshift needs -- it is inherited entirely.

    def list_tables(self) -> list[str]:
        """Physical table names in ``self.schema``, via ``svv_tables``.

        ``svv_tables`` is Redshift's permission-aware system view over all
        tables visible to the current user (local, external, and shared);
        we restrict to ``table_type = 'TABLE'`` to exclude views.
        """
        rows = self._fetchall(
            "SELECT table_name FROM svv_tables "
            "WHERE table_schema = %s AND table_type = 'TABLE' "
            "ORDER BY table_name",
            (self.schema,),
        )
        return [row[0] for row in rows]

    def _column_specs(self, table: str) -> list[tuple[str, str, bool]]:
        """Column (name, raw_type, nullable) triples for ``table``, via ``svv_columns``.

        Mirrors the parent's raw-type construction: append the declared
        character length in parentheses when present (e.g. ``varchar(256)``),
        otherwise use the bare ``data_type``.
        """
        rows = self._fetchall(
            "SELECT column_name, data_type, is_nullable, character_maximum_length "
            "FROM svv_columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position",
            (self.schema, table),
        )
        specs: list[tuple[str, str, bool]] = []
        for column_name, data_type, is_nullable, max_length in rows:
            raw_type = f"{data_type}({max_length})" if max_length is not None else data_type
            specs.append((column_name, raw_type, is_nullable == "YES"))
        return specs

    # _primary_keys: inherited from PostgresConnector. Redshift's PRIMARY KEY
    # constraints are declared/informational (never enforced on write), but
    # information_schema still reports them, so the parent's implementation
    # applies as-is -- surfaced for signal, not relied on for integrity.
