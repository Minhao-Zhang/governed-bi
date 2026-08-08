"""Weighted hybrid fusion over the channels that were **consulted**.

:func:`scale_within_channel` min-maxes to ``[0, 1]`` so channels share a scale
before fusion. Scaling is *within* a channel, never across: the ``max(lexical,
semantic)`` rule this replaced (fixed in ``5499ab2``) compared a raw BM25 score
against a raw cosine, on incomparable scales, so the semantic channel lost
essentially always.

:func:`fuse` takes the consulted set, because a channel never consulted and a
channel that scored this document zero are different facts; conflating them made
additional evidence lower a score. A consulted channel absent from ``scores``
contributes 0.0.
"""


from __future__ import annotations

from collections.abc import Collection, Mapping

__all__ = ["fuse", "scale_within_channel"]


def scale_within_channel(scores: Mapping[str, float]) -> dict[str, float]:
    """Min-max channel scores to ``[0, 1]`` over documents scored (floors at 0 for ``route``).

    Min-max rather than raw blending because the raw ranges are incomparable: BM25 after
    saturation runs 0.60–0.97 and cosine runs 0.00–0.635, so a 0.5/0.5 blend of raw values
    is the lexical score plus a small constant.

    Known residual: the floor is exactly 0.0, so a channel's weakest scored document gets
    the same value as one that channel never saw. Changing the normaliser needs its own
    measurement; it is not the monotonicity defect :func:`fuse` fixed.
    """
    if not scores:
        return {}
    low = min(scores.values())
    high = max(scores.values())
    if high <= low:
        # One document, or a tie across all of them: this channel's best evidence within
        # the facet, with nothing to spread it against.
        return {key: 1.0 for key in scores}
    span = high - low
    return {key: (value - low) / span for key, value in scores.items()}


def fuse(scores: Mapping[str, float], weights: Mapping[str, float], *,
         consulted: Collection[str]) -> float:
    """``sum(w_c * score_c) / sum(w_c)`` over ``consulted``, absent-but-consulted counted 0.0.

    ``consulted`` is the set of channels that ran for this query — a fact only the caller
    has. **A fixed denominator over it is what makes the result monotone in evidence.**
    Averaging over the channels present in ``scores`` instead made an absent channel neutral
    while a present one scoring 0.0 was maximally penalising, so at weights 0.5/0.5 an asset
    found by both channels (0.6, 0.0 -> 0.30) ranked below one found by only lexical
    (0.6 -> 0.60). A positive floor does not fix this: any weighted **mean** over a varying
    term set falls when a below-mean term is added. The mean is the defect.

    A channel in ``scores`` but not in ``consulted`` raises — including it silently would
    put numerator and denominator over different channel sets.
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
        # A consulted channel that did not score this document scores it 0.0: that is a
        # measurement by the channel, not an absence of one.
        weighted += w * float(scores.get(channel, 0.0))
        active_weight += w
    if active_weight == 0.0:
        raise ValueError("fuse requires at least one consulted channel with non-zero weight")
    return weighted / active_weight
