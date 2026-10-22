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
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def delivery_hash_for(
    context_hash: str | None,
    tool_delivered: Mapping[str, str],
) -> str | None:
    if not context_hash:
        return None
    items = sorted((str(k), str(v)) for k, v in tool_delivered.items())
    blob = context_hash + json.dumps(items, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def tool_bounds_from_state(state: Mapping[str, Any]) -> ToolBounds:
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
    def __init__(self, initial: Mapping[str, str] | None = None) -> None:
        self.tool_delivered: dict[str, str] = dict(initial or {})

    def record(self, call_id: str, payload: str) -> None:
        if call_id:
            self.tool_delivered[str(call_id)] = payload_digest(payload)

    def merge_into(
        self,
        delivery: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Delta of ``tool_delivered`` and ``delivery_hash`` only.

        A fresh delivery dict dropped ``evicted``, the only record that the char budget removed
        a retrieved table. The hash still reads ``context_hash`` off the existing delivery.
        """
        base = dict(delivery or {})
        existing = dict(base.get("tool_delivered") or {})
        existing.update(self.tool_delivered)
        return {
            "tool_delivered": existing,
            "delivery_hash": delivery_hash_for(base.get("context_hash"), existing),
        }
