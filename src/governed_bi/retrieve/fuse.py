"""Fixed ceilings, then a mean over the channels that ran. See ADR 0005."""


from __future__ import annotations

from collections.abc import Collection, Mapping

__all__ = ["fuse", "scale_to_ceiling"]


def scale_to_ceiling(value: float, *, ceiling: float) -> float:
    """``value / ceiling``, clamped to ``[0, 1]``.

    A per-query min-max made each channel's best hit 1.0, so ``route`` summed votes that hid match strength.
    Reciprocal-rank fusion is ordinal and has the same defect.
    """
    if ceiling <= 0.0:
        raise ValueError(
            f"ceiling must be positive, got {ceiling!r}. A ceiling of zero would make every "
            "score on this channel infinite or undefined, not neutral."
        )
    if value <= 0.0:
        return 0.0
    return min(1.0, value / ceiling)


def fuse(scores: Mapping[str, float], weights: Mapping[str, float], *,
         consulted: Collection[str]) -> float:
    """Weighted mean over ``consulted``. A consulted channel missing from ``scores`` counts as 0.

    Averaging only channels present in ``scores`` let extra evidence lower a score. A scored channel that was not
    consulted raises.
    """
    consulted_set = frozenset(consulted)
    if not consulted_set:
        raise ValueError(
            "fuse requires the set of channels that were consulted. It cannot be derived from "
            "`scores`: a channel missing from `scores` may have been consulted and scored this "
            "document zero, or never consulted at all, and treating those the same is what made "
            "additional evidence lower a score."
        )
    unexpected = sorted(set(scores) - consulted_set)
    if unexpected:
        raise ValueError(
            f"scored on channel(s) {unexpected} that are not in consulted={sorted(consulted_set)}"
        )

    active_weight = 0.0
    weighted = 0.0
    for channel in consulted_set:
        if channel not in weights:
            raise KeyError(f"unknown channel: {channel!r}")
        w = weights[channel]
        weighted += w * float(scores.get(channel, 0.0))
        active_weight += w
    if active_weight == 0.0:
        raise ValueError("fuse requires at least one consulted channel with non-zero weight")
    return weighted / active_weight
