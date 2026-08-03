"""Coreference rewrite node (ADR 0005 §3.3).

Turn 1 skips (``rewrite`` stays ``None`` — node did not run). Later turns are
still a no-op in F1: identity rewrite with outcome ``unchanged``. No model call.
"""

from __future__ import annotations

__all__ = ["rewrite_node"]


def rewrite_node(state: dict) -> dict:
    """Skip on first turn; otherwise record an unchanged rewrite stub."""
    if state.get("turn_index", 1) <= 1:
        return {"rewrite": None}

    q = state["question"]
    return {
        "rewrite": {
            "before": q,
            "after": q,
            "outcome": "unchanged",
        }
    }
