"""Negative-example gate node (ADR 0005 §2.7).

``negative_tau`` is UNSET until a negative corpus exists, so the gate ships
disabled. Written on every turn (total record); never ``path_kind`` hit in F1.

**It stayed a stub through the LanceDB migration, deliberately.** ADR 0005 budgets
it at "one vector lookup (~10 ms)", so it is the one declared vector-similarity site
in the tree with no implementation, and "every similarity goes through LanceDB" was
the moment to ask whether to write it. What it is missing is not a store — building
it against ``retrieve/vectors.py`` is a dozen lines — it is a **negative corpus** and
a **measured τ**. Shipping it without either would mean a threshold invented at the
call site, which is what ``register/knobs.py`` exists to prevent, and an enabled gate
comparing against an empty corpus refuses nothing while reporting that it checked.
So the absence stays honest: ``outcome: disabled`` on every turn, in the record,
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
