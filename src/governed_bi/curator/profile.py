"""Curator loop step 1 - Profile (Facts, programmatic).

Read the catalog + sample data and emit the **Facts tier** for every table and
column: physical_name, physical_type, logical_type, nullable, is_unique,
sample_values, row_count. Deterministic; no LLM; correct in every eval arm.
These are never proposed and never checked by the adversary (D10). The proposer
fills the Inference tier (descriptions, roles, joins) on top of this.
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


def profile_database(connector: "Connector", db: str) -> list[TableAsset]:
    """Emit Facts-only table assets from the live catalog.

    The Inference tier (description, role, references, reliability, confidence) is
    left empty for the proposer. Works against any :class:`Connector`; in dev that
    is the SQLite BIRD fixture.
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
                is_unique=connector.is_unique(name, c.name),
                sample_values=connector.sample_values(name, c.name),
            )
            for c in info.columns
        ]
        tables.append(
            TableAsset(
                id=f"tbl_{_slug(db)}_{_slug(name)}",
                db=db,
                physical_name=name,
                row_count=connector.row_count(name),
                columns=columns,
            )
        )
    return tables
