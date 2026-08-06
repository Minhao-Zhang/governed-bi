"""Weighted hybrid fusion, renormalised by active channels.

Absent channels are skipped, not scored as 0.0.
:func:`scale_within_channel` min-maxes to ``[0, 1]`` so channels share a scale
before fusion (floors at 0 for ``route`` aggregation).
"""


from __future__ import annotations

from collections.abc import Mapping

__all__ = ["fuse", "scale_within_channel"]


def scale_within_channel(scores: Mapping[str, float]) -> dict[str, float]:
    """Min-max channel scores to ``[0, 1]`` over documents scored (floors at 0 for ``route``).

    BM25.search still returns absolute saturated scores for the record; this scaling
    is what ``route`` sums so channels are commensurate.
    """
    if not scores:
        return {}
    low = min(scores.values())
    high = max(scores.values())
    if high <= low:
        # One document, or a tie across all of them: it is this channel's best evidence within
        # this facet and there is nothing to spread it against.
        return {key: 1.0 for key in scores}
    span = high - low
    return {key: (value - low) / span for key, value in scores.items()}


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
