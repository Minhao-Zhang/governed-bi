"""Negative-example gate node (ADR 0005 §2.7).

**A stub unconditionally.** :func:`negative_node` discards its state (its body opens
``_ = state``) and returns ``outcome: disabled`` without reading ``negative_tau`` or the
corpus, so the ``decline`` branch is unreachable whatever is configured and whatever is
curated. Written on every turn (total record); never ``path_kind`` hit in F1.

What is missing before the node could read anything is a **negative corpus** and a
**measured τ**: a threshold invented at the call site is what ``register/knobs.py`` exists to
prevent, and ``negative_tau`` ships ``UNSET`` (declared in ``register/knobs.py``) because no τ has been
measured. An enabled gate comparing against an empty corpus would refuse nothing while
reporting that it checked; ``outcome: disabled`` on every turn keeps the absence in the record
where a reader can see it.

**Do not delete this node as dead code without an operator's decision — it is load-bearing for
measurement, not for behaviour.** Checked 2026-08-26, when the ``rewrite`` rail beside it was
deleted for being a genuine no-op. This one is not the same case:

* ``register/record.py``'s ``negative`` field carries ``gate="no negative_gate
  error_failed_open"``, and ``GATE_CONDITIONS`` is *derived* from exactly the fields with a
  ``gate=``. ``measure/gates.py::quotable`` requires all seven to pass, so dropping the field
  drops the bar to six — and the import-time closure assertion at the foot of that module then
  forces the matching ``GATE_IMPLEMENTATIONS`` entry out with it.
* Keeping the field while deleting the node is worse, not a compromise.
  ``eval/projection.py`` computes the counter as ``bool(negative.get("outcome") ==
  "error_failed_open")`` over ``state.get("negative") or record.get("negative") or {}``, so with
  nothing writing the channel it is a measured ``False`` on every row and the gate passes
  forever. That is the always-pass gate ``n_re_served`` was demoted out of ``Tier.health`` for
  being, dressed as a check.

So the honest choice is seven gates with a stub behind one of them, or six gates. Trading a
quotability gate for 31 lines is the operator's call in a repository whose discipline is
refusing to quote a number.
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
