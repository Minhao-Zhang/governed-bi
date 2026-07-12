"""Curator loop step 1 - Profile (Facts, programmatic).

Emit the **Facts tier** for every table and column, cheaply enough to run against
a large data lake: physical_name, physical_type, logical_type, nullable,
catalog-declared uniqueness (primary key), and a few sample values. Deterministic;
no LLM.

**No full-scan aggregates.** We assume table scans are impractical at data-lake
scale, so profiling never runs a uniqueness ``COUNT(DISTINCT)`` or a ``COUNT(*)``
row count. ``is_unique`` reflects only the catalog's declared primary key (a free
metadata read); ``row_count`` is left unset. Sample values come from a bounded
``LIMIT`` (cheap). The scanned facts - true (non-PK) uniqueness and row count -
are opt-in per column via :func:`enrich.enrich_table` (partial indexing), for
when a specific table earns the cost.

These Facts are never proposed and never checked by the adversary (D10). The
proposer fills the Inference tier (descriptions, roles, joins) on top.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..corpus.schemas import Column, LogicalType, TableAsset

if TYPE_CHECKING:
    from ..gateway.connectors.base import Connector


def _slug(name: str) -> str:
    """Make a regex-valid id segment from a real name (ids are lowercase a-z0-9_)."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _logical_type(raw: str) -> LogicalType:
    """Map a raw catalog type to a normalized, portable logical type (best-effort,
    following SQLite-style type-affinity rules)."""
    t = raw.strip().upper()
    if "INT" in t:
        return LogicalType.integer
    if any(k in t for k in ("CHAR", "CLOB", "TEXT")):
        return LogicalType.string
    if "BOOL" in t:
        return LogicalType.boolean
    if any(k in t for k in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
        return LogicalType.decimal
    if "DATETIME" in t or "TIMESTAMP" in t or "TIME" in t:
        return LogicalType.datetime
    if "DATE" in t:
        return LogicalType.date
    return LogicalType.string


def profile_database(
    connector: "Connector", schema: str, *, sample_limit: int = 5
) -> list[TableAsset]:
    """Emit bare-minimum Facts-only table assets from the catalog + cheap samples.

    Per column: name, dtype, logical type, nullability, catalog-declared
    uniqueness (the primary-key flag), and up to ``sample_limit`` sample values (a
    bounded ``LIMIT``). Runs no ``COUNT(DISTINCT)`` and no ``COUNT(*)``, so it is
    safe at data-lake scale; ``row_count`` is left unset and non-PK uniqueness is
    left to :func:`enrich.enrich_table`. The Inference tier (description, role,
    references, reliability, confidence) is left empty for the proposer. Works
    against any :class:`Connector`. ``schema`` is the corpus namespace written onto
    each :class:`TableAsset` (and used in asset ids).
    """
    tables: list[TableAsset] = []
    for name in connector.list_tables():
        info = connector.describe_table(name)
        columns = [
            Column(
                physical_name=c.name,
                physical_type=c.data_type,
                logical_type=_logical_type(c.data_type),
                nullable=c.nullable,
                is_unique=c.primary_key,  # catalog-declared PK; no scan
                sample_values=connector.sample_values(name, c.name, limit=sample_limit),
            )
            for c in info.columns
        ]
        tables.append(
            TableAsset(
                id=f"tbl_{_slug(schema)}_{_slug(name)}",
                schema=schema,
                physical_name=name,
                columns=columns,  # row_count left unset (no COUNT(*))
            )
        )
    return tables
