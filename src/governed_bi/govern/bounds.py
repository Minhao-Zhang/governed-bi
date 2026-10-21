"""Tool bounds and the retrieved tables (ADR 0006 §8).

A tool that grants privilege must have a bound the model cannot widen.
:class:`ToolBounds` is frozen with no widening method; ``retrieved_tables`` is closed at
``connect`` (ADR 0005). Out-of-scope and non-existent share :data:`OUT_OF_SCOPE_MESSAGE`.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["OUT_OF_SCOPE_MESSAGE", "ToolBounds"]

#: The single reply for "you may not" **and** for "there is no such thing".
OUT_OF_SCOPE_MESSAGE = (
    "That identifier is not available in this conversation. Work from the assets in "
    "the context you were given."
)


@dataclass(frozen=True, slots=True)
class ToolBounds:
    """What this turn's tools may touch. Closed at ``connect``, never widened."""

    #: **Asset ids**, not ``{schema}.{physical_name}`` (``serve/context.py::_tool_key``). Seeded
    #: by ``route_node`` from the post-budget table set, so a table the retrieval cap dropped is
    #: refused ``r_table_not_retrieved``; ADR 0006 section 8 holds that open decision.
    retrieved_tables: frozenset[str] = frozenset()
    #: Asset ids in this turn's ``hits ∪ pulled_in``.
    #: ``read_body``'s bound.
    readable_assets: frozenset[str] = frozenset()

    def may_read_body(self, asset_id: str) -> bool:
        """``read_body`` is corpus prose, not data, and is bounded by ``readable_assets``."""
        return asset_id in self.readable_assets

    def may_inspect_schema(self, table_key: str) -> bool:
        """``inspect_schema`` is bounded by the retrieved tables."""
        return table_key in self.retrieved_tables

    def may_sample(self, column_id: str) -> bool:
        """``sample_rows`` takes a **column id**, not a name (§7).

        A model-supplied column name would be interpolated into ``SELECT {column} FROM
        {table}`` — identifiers cannot be bound as parameters — giving an injection
        surface with no parse, function or column layer and no ledger entry. The bound
        is the column's *table*: a column id whose table this turn does not license is
        out of scope.
        """
        table = column_id.rsplit(".", 1)[0]
        return bool(table) and table != column_id and table in self.retrieved_tables
