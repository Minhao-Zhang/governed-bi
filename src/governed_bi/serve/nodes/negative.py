"""Negative-example gate node (ADR 0005 §2.7).

**A stub unconditionally.** :func:`negative_node` discards its state (``negative.py:23``:
``_ = state``) and returns ``outcome: disabled`` without reading ``negative_tau`` or the
corpus, so the ``decline`` branch is unreachable whatever is configured and whatever is
curated. Written on every turn (total record); never ``path_kind`` hit in F1.

What is missing before the node could read anything is a **negative corpus** and a
**measured τ**: a threshold invented at the call site is what ``register/knobs.py`` exists to
prevent, and ``negative_tau`` ships ``UNSET`` (``register/knobs.py:155``) because no τ has been
measured. An enabled gate comparing against an empty corpus would refuse nothing while
reporting that it checked; ``outcome: disabled`` on every turn keeps the absence in the record
where a reader can see it.
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
