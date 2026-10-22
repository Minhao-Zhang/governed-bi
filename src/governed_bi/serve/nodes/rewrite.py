"""Coreference rewrite (ADR 0005 §3.3).

The first turn leaves ``rewrite`` unset. Later turns record an identity rewrite
and do not call a model.
"""

from __future__ import annotations

__all__ = ["rewrite_node"]


def rewrite_node(state: dict) -> dict:
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
