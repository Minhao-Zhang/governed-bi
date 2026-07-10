"""Database connectors: the per-dialect read-only data boundary.

SQLite is implemented and tested against the committed fixture. Postgres and
Redshift are implemented too (``information_schema`` / ``svv_*`` introspection,
read-only execution with a statement timeout) behind the ``postgres`` /
``redshift`` optional extras, and unit-tested offline against a fake connection -
but not yet run against a live server/cluster. The DB drivers are imported
lazily, so importing these classes never requires the extra to be installed. See
``base.Connector`` for the interface every dialect implements.
"""

from __future__ import annotations

from .base import ColumnInfo, Connector, Dialect, QueryResult, TableInfo
from .postgres import PostgresConnector
from .redshift import RedshiftConnector
from .sqlite import SqliteConnector

__all__ = [
    "ColumnInfo",
    "Connector",
    "Dialect",
    "PostgresConnector",
    "QueryResult",
    "RedshiftConnector",
    "SqliteConnector",
    "TableInfo",
]
