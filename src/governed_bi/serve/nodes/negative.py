"""Negative-example gate node (ADR 0005 §2.7).

``negative_tau`` is UNSET until a negative corpus exists, so the gate ships
disabled. Written on every turn (total record); never ``path_kind`` hit in F1.
"""

from __future__ import annotations

__all__ = ["negative_node"]


def negative_node(state: dict) -> dict:
    """Always disabled in F1 — no τ, no score, no match."""
    _ = state
    return {
        "negative": {
            "outcome": "disabled",
            "tau": None,
            "top_score": None,
            "matched_id": None,
        }
    }
