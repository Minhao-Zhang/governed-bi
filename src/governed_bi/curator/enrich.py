"""Opt-in deep column profiling (the scans :mod:`profile` deliberately skips).

:func:`profile.profile_database` stays cheap for a data lake: catalog facts plus a
bounded sample, no full scans. When a specific table earns the cost, this module
backfills the expensive Facts - true (non-PK) uniqueness via ``COUNT(DISTINCT)``
and the table ``COUNT(*)`` row count - for **selected columns only** (partial
indexing), so you never have to scan a whole wide table to learn about one column.

Deliberate by design: call this for the columns a question or a reviewer actually
needs, not by default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..corpus.schemas import TableAsset
    from ..gateway.connectors.base import Connector


def enrich_table(
    connector: "Connector",
    table: "TableAsset",
    *,
    columns: list[str] | None = None,
    include_row_count: bool = True,
) -> "TableAsset":
    """Backfill scanned Facts for ``table``, for selected columns only.

    Recomputes ``is_unique`` from an actual ``COUNT(DISTINCT)`` scan for each
    column named in ``columns`` (partial indexing); ``columns=None`` enriches
    every column. Optionally sets the table ``row_count`` from a ``COUNT(*)``.
    The input asset is never mutated - a new :class:`TableAsset` is returned,
    with untouched columns passed through unchanged. ``connector`` must be scoped
    to the table's database/schema.

    These are full-scan operations; call deliberately (see :mod:`profile`).
    """
    targets = set(columns) if columns is not None else {c.physical_name for c in table.columns}
    unknown = targets - {c.physical_name for c in table.columns}
    if unknown:
        raise ValueError(f"columns not in {table.physical_name!r}: {sorted(unknown)}")

    name = table.physical_name
    new_columns = [
        col.model_copy(update={"is_unique": connector.is_unique(name, col.physical_name)})
        if col.physical_name in targets
        else col
        for col in table.columns
    ]

    update: dict = {"columns": new_columns}
    if include_row_count:
        update["row_count"] = connector.row_count(name)
    return table.model_copy(update=update)
