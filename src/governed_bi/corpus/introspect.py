"""Shapes the seed reads. Kept here so the seed does not import ``datasource``. See ADR 0005.

Named ``Introspected*`` because ``ColumnInfo`` and ``TableInfo`` belong to ``ports``.
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
