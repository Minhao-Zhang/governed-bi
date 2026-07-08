"""Postgres connector (seam).

Not implemented yet. The interface is designed to fit it: catalog introspection
via ``information_schema`` and read-only execution via ``psycopg`` with a
statement timeout and a read-only transaction. Install the optional extra when
implementing:

    uv sync --extra postgres

``psycopg`` is imported lazily inside the implementation so the core install
stays free of DB drivers.
"""

from __future__ import annotations

from typing import Any

from .base import Connector, Dialect, QueryResult, TableInfo

_SEAM = (
    "PostgresConnector is a seam. Implement against information_schema with a "
    "read-only transaction + statement_timeout, and install the extra: "
    "uv sync --extra postgres"
)


class PostgresConnector(Connector):
    dialect = Dialect.postgres

    def __init__(self, dsn: str, **kwargs: Any) -> None:
        raise NotImplementedError(_SEAM)

    def list_tables(self) -> list[str]:
        raise NotImplementedError(_SEAM)

    def describe_table(self, table: str) -> TableInfo:
        raise NotImplementedError(_SEAM)

    def row_count(self, table: str) -> int:
        raise NotImplementedError(_SEAM)

    def sample_values(self, table: str, column: str, *, limit: int = 5) -> list[Any]:
        raise NotImplementedError(_SEAM)

    def is_unique(self, table: str, column: str) -> bool:
        raise NotImplementedError(_SEAM)

    def execute(self, sql: str, *, max_rows: int = 1000, timeout_s: float = 30.0) -> QueryResult:
        raise NotImplementedError(_SEAM)
