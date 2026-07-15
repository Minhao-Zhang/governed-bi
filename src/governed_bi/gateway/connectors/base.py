"""Connector interface: the read-only data boundary for one database.

A connector has two jobs (Architecture §3-4):

1. **Catalog introspection** (tables / columns / types / samples / row counts,
   uniqueness) for the curator's Facts tier and the physical-existence check.
2. **Guarded read-only execution** (forced row cap + timeout) for the gateway.

It is **dialect-aware** so ``sqlglot`` can parse, validate, and transpile SQL
against the right grammar, and so catalog introspection uses the right system
tables. SQLite is proven against the committed fixture; **Postgres is exercised
live** by the eval harness (``eval/run_experiment.py`` runs the three arms against
a local BIRD-Obfuscation Postgres) and is also unit-tested offline against a fake
connection; Redshift shares the Postgres path but is **not yet run against a live
cluster**.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Dialect(str, Enum):
    """Names match sqlglot dialects, so a connector's dialect drives parsing."""

    sqlite = "sqlite"
    postgres = "postgres"
    redshift = "redshift"


@dataclass(frozen=True)
class ColumnInfo:
    """A column as the catalog reports it (Facts tier, raw)."""

    name: str  # physical name in the DB
    data_type: str  # raw catalog type, dialect-specific (e.g. "varchar(20)")
    nullable: bool
    primary_key: bool = False


@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    row_count: int
    truncated: bool = False  # True if the row cap clipped the result


class Connector(ABC):
    """Read-only connection to one database. Concrete per dialect."""

    dialect: Dialect

    # -- catalog introspection --------------------------------------------- #
    @abstractmethod
    def list_schemas(self) -> list[str]:
        """Every user schema (namespace) this connection can introspect.

        Schema-bearing engines (Postgres/Redshift) enumerate their user schemas -
        one per db_id in the BIRD-Obfuscation instances - so one connector can
        span the whole database (D15). Schemaless engines (SQLite) have no schema
        level, so they return a single logical namespace.
        """

    @abstractmethod
    def list_tables(self, schema: str | None = None) -> list[str]:
        """Physical table names, excluding system tables.

        ``schema`` selects which namespace to introspect; ``None`` (the default)
        means the connector's pinned schema, so single-schema callers are
        unaffected. Schemaless engines ignore it.
        """

    @abstractmethod
    def describe_table(self, table: str, schema: str | None = None) -> TableInfo:
        """Columns (name, raw type, nullability, PK flag) for one table.

        ``schema`` defaults to the connector's pinned schema (see
        :meth:`list_tables`)."""

    @abstractmethod
    def row_count(self, table: str, schema: str | None = None) -> int: ...

    @abstractmethod
    def sample_values(
        self, table: str, column: str, *, limit: int = 5, schema: str | None = None
    ) -> list[Any]:
        """The first ``limit`` values of the column (a plain ``LIMIT``, no scan).

        Cheap by design for data-lake scale: it stops after ``limit`` rows and
        does not de-duplicate or filter nulls, so values may repeat or be null.
        ``schema`` defaults to the connector's pinned schema.
        """

    @abstractmethod
    def is_unique(self, table: str, column: str, schema: str | None = None) -> bool:
        """Whether the column's non-null values are distinct (key candidate).

        ``schema`` defaults to the connector's pinned schema."""

    # -- execution --------------------------------------------------------- #
    @abstractmethod
    def execute(self, sql: str, *, max_rows: int = 1000, timeout_s: float = 30.0) -> QueryResult:
        """Run read-only SQL with a forced row cap and timeout. Writes must fail."""

    def explain(self, sql: str) -> str:
        """Dialect-specific query plan (guardrail L5 input). Optional per dialect."""
        raise NotImplementedError(f"explain() is not implemented for dialect {self.dialect}")

    def close(self) -> None:  # pragma: no cover - trivial default
        """Release the underlying connection. Safe no-op by default."""
