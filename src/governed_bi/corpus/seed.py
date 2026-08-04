"""Model-free seed: introspection → valid assets (ADR 0005 §1.7).

Deterministic, non-empty ``summary`` on every asset so steps 6–9 are measurable
before a curator exists. Does not author ``reliability`` from absence.
"""

from __future__ import annotations

from .identity import derive_column_id, join_id
from .identity import table_id as table_id_for
from .introspect import Introspection
from .schema import (
    Asset,
    ColumnAsset,
    JoinAsset,
    SchemaAsset,
    TableAsset,
)
from .validate import Problem, problems_with

__all__ = ["seed"]


def seed(introspection: Introspection, schema: str) -> tuple[list[Asset], list[Problem]]:
    """Build a seeded corpus for ``schema`` from ``introspection``. Zero model calls."""
    tables = sorted(introspection.tables, key=lambda t: t.physical_name)
    table_names = [t.physical_name for t in tables]
    assets: list[Asset] = []

    name_list = ", ".join(table_names)
    assets.append(
        SchemaAsset(
            id=schema,
            name=schema,
            summary=f"{schema} — {len(tables)} tables: {name_list}"[:250],
        )
    )

    for table in tables:
        # The convention is declared once, in identity.py: the endpoint reconciliation
        # in retrieve/structure.py keys on it, and a second spelling there would bind
        # an edge to the wrong table rather than merely losing it (ADR 0005 §2.8.2).
        table_id = table_id_for(schema, table.physical_name)
        col_names = [c.physical_name for c in table.columns]
        col_list = ", ".join(col_names)
        summary = (
            f"{table.physical_name} ({len(table.columns)} columns: {col_list})"
        )[:250]
        column_ids = tuple(
            derive_column_id(table_id, column.physical_name) for column in table.columns
        )
        assets.append(
            TableAsset(
                id=table_id,
                schema=schema,
                physical_name=table.physical_name,
                summary=summary,
                columns=column_ids,
            )
        )
        for column in table.columns:
            ctype = (column.physical_type or "text").lower()
            assets.append(
                ColumnAsset(
                    id=derive_column_id(table_id, column.physical_name),
                    schema=schema,
                    parent_table=table_id,  # the table's id, not its bare name (0008 D4)
                    physical_name=column.physical_name,
                    summary=f"{table.physical_name}.{column.physical_name} ({ctype})"[:250],
                    physical_type=column.physical_type,
                    nullable=column.nullable,
                )
            )

    for fk in sorted(
        introspection.foreign_keys,
        key=lambda f: (f.from_table, f.to_table, f.from_columns, f.to_columns),
    ):
        on = " AND ".join(
            f"{fk.from_table}.{left} = {fk.to_table}.{right}"
            for left, right in zip(fk.from_columns, fk.to_columns, strict=True)
        )
        jid = join_id(schema, fk.from_table, fk.to_table, on)
        assets.append(
            JoinAsset(
                id=jid,
                left_table=fk.from_table,
                right_table=fk.to_table,
                on=on,
                summary=(
                    f"{fk.from_table} joins {fk.to_table} on "
                    + ", ".join(fk.from_columns)
                )[:250],
            )
        )

    problems = [
        Problem(where=getattr(asset, "id", "<asset>"), reason=reason)
        for asset in assets
        for reason in problems_with(asset)
    ]
    return assets, problems
