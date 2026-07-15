"""Database connectors: the per-dialect read-only data boundary.

SQLite is implemented and tested against the committed fixture. Postgres
(``information_schema`` introspection, read-only execution with a statement
timeout) is **exercised live** by the eval harness (``eval/run_experiment.py``,
against a local BIRD-Obfuscation Postgres) and is also unit-tested offline against
a fake connection. Redshift reuses the Postgres path (``svv_*`` introspection) but
is **not yet run against a live cluster**. The DB drivers are imported lazily, so
importing these classes never requires the driver to be installed. See
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
