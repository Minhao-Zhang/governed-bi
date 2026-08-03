"""Weighted hybrid fusion, renormalised by active channels.

A channel absent from ``scores`` did not run — it is skipped, not treated as
0.0. That distinction is load-bearing: scoring zero is evidence of irrelevance;
not running is no evidence at all.
"""

from __future__ import annotations

__all__ = ["fuse"]


def fuse(scores: dict[str, float], weights: dict[str, float]) -> float:
    """``sum(w_c * score_c) / sum(w_c)`` over channels present in ``scores``."""
    active_weight = 0.0
    weighted = 0.0
    for channel, score in scores.items():
        if channel not in weights:
            raise KeyError(f"unknown channel: {channel!r}")
        w = weights[channel]
        weighted += w * score
        active_weight += w
    if active_weight == 0.0:
        raise ValueError("fuse requires at least one active channel with non-zero weight")
    return weighted / active_weight
