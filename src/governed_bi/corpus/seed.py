"""Model-free seed: introspection → valid assets (ADR 0005 §1.7).

Deterministic non-empty ``summary`` on every asset. Does not author
``reliability`` from absence.
"""


from __future__ import annotations

from governed_bi.register.knobs import knob_default

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

__all__ = ["seed", "fit_summary"]


def fit_summary(head: str, entries: list[str], *, joiner: str = ", ", tail: str = "") -> str:
    """``head`` plus as many whole ``entries`` as fit the cap; never mid-token truncate.

    Dropped count appears as ``(+N more)``. ``tail`` is measured inside the loop.
    """
    cap = int(knob_default("summary_max_chars"))
    kept = list(entries)
    while True:
        dropped = len(entries) - len(kept)
        suffix = f" (+{dropped} more)" if dropped else ""
        body = joiner.join(kept)
        candidate = f"{head}{body}{suffix}{tail}" if body else f"{head.rstrip(': ')}{suffix}{tail}"
        if len(candidate) <= cap or not kept:
            return candidate
        kept.pop()


def seed(introspection: Introspection, schema: str) -> tuple[list[Asset], list[Problem]]:
    """Build a seeded corpus for ``schema`` from ``introspection``. Zero model calls."""
    tables = sorted(introspection.tables, key=lambda t: t.physical_name)
    table_names = [t.physical_name for t in tables]
    assets: list[Asset] = []

    assets.append(
        SchemaAsset(
            id=schema,
            name=schema,
            summary=fit_summary(f"{schema} — {len(tables)} tables: ", table_names),
        )
    )

    for table in tables:
        # The convention is declared once, in identity.py: endpoint reconciliation in
        # retrieve/structure.py keys on it, and a second spelling there binds an edge to
        # the wrong table rather than merely losing it (ADR 0005 §2.8.2).
        table_id = table_id_for(schema, table.physical_name)
        col_names = [c.physical_name for c in table.columns]
        summary = fit_summary(
            f"{table.physical_name} ({len(table.columns)} columns: ", col_names, tail=")"
        )
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
                    # Not run through `fit_summary`: one identifier and one type, nothing
                    # to drop, so an over-cap value means a pathological physical name and
                    # belongs to the validator rather than to a silent slice.
                    summary=f"{table.physical_name}.{column.physical_name} ({ctype})",
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
                summary=fit_summary(
                    f"{fk.from_table} joins {fk.to_table} on ", list(fk.from_columns)
                ),
            )
        )

    problems = [
        Problem(where=getattr(asset, "id", "<asset>"), reason=reason)
        for asset in assets
        for reason in problems_with(asset)
    ]
    return assets, problems
