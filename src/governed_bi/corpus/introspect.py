"""Introspection shapes consumed by the seed (ADR 0005 §1.7).

Live in ``corpus/`` rather than ``datasource/`` so the seed can import them
without an upward layer violation (corpus sits below datasource).

Named ``Introspected*`` rather than ``ColumnInfo`` / ``TableInfo`` — those names
already belong to the ``ports`` Protocols for ``Connector.describe_table``.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "IntrospectedColumn",
    "IntrospectedTable",
    "ForeignKeyInfo",
    "Introspection",
]


@dataclass(frozen=True, slots=True)
class IntrospectedColumn:
    physical_name: str
    physical_type: str
    nullable: bool = True


@dataclass(frozen=True, slots=True)
class IntrospectedTable:
    physical_name: str
    columns: tuple[IntrospectedColumn, ...] = ()


@dataclass(frozen=True, slots=True)
class ForeignKeyInfo:
    from_table: str
    from_columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Introspection:
    tables: tuple[IntrospectedTable, ...] = ()
    foreign_keys: tuple[ForeignKeyInfo, ...] = ()
