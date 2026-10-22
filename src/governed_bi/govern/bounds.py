"""Frozen, with no widening method. See ADR 0006. Denial and absence share one message so they are not distinguishable.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["OUT_OF_SCOPE_MESSAGE", "ToolBounds"]

#: One reply for denial and for a missing identifier.
OUT_OF_SCOPE_MESSAGE = (
    "That identifier is not available in this conversation. Work from the assets in "
    "the context you were given."
)


@dataclass(frozen=True, slots=True)
class ToolBounds:
    """Closed at ``connect``. No widening method."""

    #: Asset ids, not ``schema.physical_name``. A table the retrieval cap dropped is absent
    #: and refused. See ADR 0006.
    retrieved_tables: frozenset[str] = frozenset()
    #: Asset ids this turn may read as prose.
    readable_assets: frozenset[str] = frozenset()

    def may_read_body(self, asset_id: str) -> bool:
        return asset_id in self.readable_assets

    def may_inspect_schema(self, table_key: str) -> bool:
        return table_key in self.retrieved_tables

    def may_sample(self, column_id: str) -> bool:
        """A column id, not a name: a name would be interpolated. The bound is the column's table."""
        table = column_id.rsplit(".", 1)[0]
        return bool(table) and table != column_id and table in self.retrieved_tables
