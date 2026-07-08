"""Database connectors: the per-dialect read-only data boundary.

SQLite is implemented and tested; Postgres and Redshift are seams behind the
``postgres`` / ``redshift`` optional extras. See ``base.Connector`` for the
interface every dialect implements.
"""

from __future__ import annotations

from .base import ColumnInfo, Connector, Dialect, QueryResult, TableInfo
from .sqlite import SqliteConnector

__all__ = [
    "ColumnInfo",
    "Connector",
    "Dialect",
    "QueryResult",
    "SqliteConnector",
    "TableInfo",
]
