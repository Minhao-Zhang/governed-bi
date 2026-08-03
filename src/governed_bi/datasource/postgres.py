"""Postgres connector adapter (BIRD obfuscated path).

Session settings required by ADR 0006 §10 are applied on open:
``default_transaction_read_only = on`` and ``synchronize_seqscans = off``.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..corpus.introspect import IntrospectedTable, Introspection
from .errors import ConnectionError

__all__ = ["PostgresConnector"]


class PostgresConnector:
    """Thin adapter. Wired when a live DSN is available; SQLite covers parcel C."""

    def __init__(self, dsn: str, *, max_rows: int = 200_000) -> None:
        self._dsn = dsn
        self._max_rows = max_rows
        self._conn: Any = None

    def __enter__(self) -> PostgresConnector:
        self._connect()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def dialect(self) -> str:
        return "postgres"

    def _connect(self) -> Any:
        if self._conn is not None:
            return self._conn
        try:
            import psycopg
        except ImportError as err:
            raise ConnectionError("psycopg is required for PostgresConnector") from err
        try:
            conn = psycopg.connect(self._dsn, autocommit=True)
            conn.execute("SET default_transaction_read_only = on")
            conn.execute("SET synchronize_seqscans = off")
        except Exception as err:
            raise ConnectionError(str(err)) from err
        self._conn = conn
        return conn

    def execute(
        self, sql: str, *, max_rows: int | None = None
    ) -> tuple[Sequence[str], Sequence[tuple[Any, ...]], bool]:
        raise ConnectionError("PostgresConnector.execute is not exercised in this tranche")

    def introspect(self) -> Introspection:
        raise ConnectionError("PostgresConnector.introspect is not exercised in this tranche")

    def list_tables(self) -> Sequence[str]:
        raise ConnectionError("PostgresConnector.list_tables is not exercised in this tranche")

    def describe_table(self, name: str) -> IntrospectedTable:
        raise ConnectionError("PostgresConnector.describe_table is not exercised in this tranche")

    def sample_values(self, table: str, column: str, *, limit: int) -> Sequence[Any]:
        raise ConnectionError("PostgresConnector.sample_values is not exercised in this tranche")
