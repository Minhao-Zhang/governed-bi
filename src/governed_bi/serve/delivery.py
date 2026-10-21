"""Tool delivery tracker + delivery_hash (ADR 0005 §3.6)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

__all__ = [
    "DeliveryTracker",
    "delivery_hash_for",
    "payload_digest",
    "tool_bounds_from_state",
]

from governed_bi.govern.bounds import ToolBounds


def payload_digest(payload: str) -> str:
    """``sha256(payload)[:16]`` for ``tool_delivered`` values."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def delivery_hash_for(
    context_hash: str | None,
    tool_delivered: Mapping[str, str],
) -> str | None:
    """``sha256(context_hash + sorted tool_delivered)``; None when no context_hash."""
    if not context_hash:
        return None
    items = sorted((str(k), str(v)) for k, v in tool_delivered.items())
    blob = context_hash + json.dumps(items, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def tool_bounds_from_state(state: Mapping[str, Any]) -> ToolBounds:
    """Frozen bounds from ``retrieved_tables`` + ``retrieved`` (hits ∪ pulled_in)."""
    retrieved_tables = frozenset(str(x) for x in (state.get("retrieved_tables") or ()))
    retrieved = state.get("retrieved") or {}
    readable: set[str] = set()
    if isinstance(retrieved, Mapping):
        readable.update(str(k) for k in (retrieved.get("selected") or {}))
        readable.update(str(k) for k in (retrieved.get("pulled_in") or {}))
        for group in (retrieved.get("attributions") or {}).values():
            for hit in group or ():
                if isinstance(hit, Mapping) and hit.get("asset_id") is not None:
                    readable.add(str(hit["asset_id"]))
                else:
                    aid = getattr(hit, "asset_id", None)
                    if aid is not None:
                        readable.add(str(aid))
    return ToolBounds(retrieved_tables=retrieved_tables, readable_assets=frozenset(readable))


class DeliveryTracker:
    """Mutable ``tool_delivered`` map for the duration of ``agent_core``."""

    def __init__(self, initial: Mapping[str, str] | None = None) -> None:
        self.tool_delivered: dict[str, str] = dict(initial or {})

    def record(self, call_id: str, payload: str) -> None:
        if call_id:
            self.tool_delivered[str(call_id)] = payload_digest(payload)

    def merge_into(
        self,
        delivery: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """The ``delivery`` **delta** this turn's tools add: ``tool_delivered`` and its hash.

        Two keys, not four, and the two are exactly what ``agent_core`` decides.
        :func:`~governed_bi.serve.state.merge_delta` carries ``context_block``,
        ``context_hash`` and ``evicted`` because they are already in the channel.

        **This function used to rebuild the record.** It returned a fresh four-key dict, so
        ``assemble``'s ``evicted`` — the only record that the char budget dropped a retrieved_tables
        table before the model ever saw it — was destroyed here, mid-turn, on every turn that
        had one. It is the reason ``table_coverage`` reads as an EX ceiling and is really a
        licensing figure: a table can be routed, retrieved_tables, counted as covered, and then
        evicted for space with nothing anywhere saying so. Measured once it survived: 1.4% of
        turns, bodies only. Rare -- but "rare" was not knowable while this deleted it.

        The first fix carried that one key by name. Writing a delta is the same fix without a
        list to keep: the identical loss was live on ``retrieved`` at the same time, one
        channel over, and a named carry here could never have covered it.

        ``delivery`` is still read, because ``delivery_hash`` digests the *whole* delivery —
        the context hash the block was rendered to, plus every tool payload — so the hash
        cannot be computed from the delta alone.
        """
        base = dict(delivery or {})
        existing = dict(base.get("tool_delivered") or {})
        existing.update(self.tool_delivered)
        return {
            "tool_delivered": existing,
            "delivery_hash": delivery_hash_for(base.get("context_hash"), existing),
        }
