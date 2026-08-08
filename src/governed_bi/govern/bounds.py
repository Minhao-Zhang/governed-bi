"""Tool bounds and the licensed set (ADR 0006 §8).

A tool that grants privilege must have a bound the model cannot widen.
:class:`ToolBounds` is frozen with no widening method; ``licensed`` is closed at
``connect`` (ADR 0005). Out-of-scope and non-existent share
:data:`OUT_OF_SCOPE_MESSAGE`. :func:`resume_authorised` is the auth primitive
(a ``thread_id`` is not a capability).
"""


from __future__ import annotations

import hmac
from dataclasses import dataclass

__all__ = ["OUT_OF_SCOPE_MESSAGE", "ToolBounds", "resume_authorised"]

#: The single reply for "you may not" **and** for "there is no such thing".
OUT_OF_SCOPE_MESSAGE = (
    "That identifier is not available in this conversation. Work from the assets in "
    "the context you were given."
)


@dataclass(frozen=True, slots=True)
class ToolBounds:
    """What this turn's tools may touch. Closed at ``connect``, never widened."""

    #: Table keys (``{schema}.{physical_name}``) this turn licenses.
    licensed: frozenset[str] = frozenset()
    #: Asset ids in this turn's ``hits ∪ pulled_in``. ``read_body``'s bound.
    readable_assets: frozenset[str] = frozenset()

    def may_read_body(self, asset_id: str) -> bool:
        return asset_id in self.readable_assets

    def may_inspect_schema(self, table_key: str) -> bool:
        return table_key in self.licensed

    def may_sample(self, column_id: str) -> bool:
        """``sample_rows`` takes a **column id**, not a name (§7).

        A model-supplied column name would be interpolated into ``SELECT {column} FROM
        {table}`` — identifiers cannot be bound as parameters — giving an injection
        surface with no parse, function or column layer and no ledger entry. The bound
        is the column's *table*: a column id whose table this turn does not license is
        out of scope.
        """
        table = column_id.rsplit(".", 1)[0]
        return bool(table) and table != column_id and table in self.licensed


def resume_authorised(*, stored_identity: str | None, caller_identity: str | None) -> bool:
    """Constant-time identity check (``hmac.compare_digest``)."""
    if not stored_identity or not caller_identity:
        return False
    return hmac.compare_digest(stored_identity, caller_identity)
