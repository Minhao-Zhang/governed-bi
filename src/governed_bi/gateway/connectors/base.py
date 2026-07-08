"""Connector interface: the read-only data boundary for one database.

A connector has two jobs (Architecture §3-4):

1. **Catalog introspection** (tables / columns / types / samples / row counts,
   uniqueness) for the curator's Facts tier and the physical-existence check.
2. **Guarded read-only execution** (forced row cap + timeout) for the gateway.

It is **dialect-aware** so ``sqlglot`` can parse, validate, and transpile SQL
against the right grammar, and so catalog introspection uses the right system
tables. SQLite is implemented and tested; Postgres and Redshift are seams (see
their modules) filled in behind optional dependency extras.
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
    def list_tables(self) -> list[str]:
        """Physical table names, excluding system tables."""

    @abstractmethod
    def describe_table(self, table: str) -> TableInfo:
        """Columns (name, raw type, nullability, PK flag) for one table."""

    @abstractmethod
    def row_count(self, table: str) -> int: ...

    @abstractmethod
    def sample_values(self, table: str, column: str, *, limit: int = 5) -> list[Any]:
        """A few distinct non-null values, for the Facts tier."""

    @abstractmethod
    def is_unique(self, table: str, column: str) -> bool:
        """Whether the column's non-null values are distinct (key candidate)."""

    # -- execution --------------------------------------------------------- #
    @abstractmethod
    def execute(self, sql: str, *, max_rows: int = 1000, timeout_s: float = 30.0) -> QueryResult:
        """Run read-only SQL with a forced row cap and timeout. Writes must fail."""

    def explain(self, sql: str) -> str:
        """Dialect-specific query plan (guardrail L5 input). Optional per dialect."""
        raise NotImplementedError(f"explain() is not implemented for dialect {self.dialect}")

    def close(self) -> None:  # pragma: no cover - trivial default
        """Release the underlying connection. Safe no-op by default."""
