"""Weighted hybrid fusion over the channels that were **consulted**.

:func:`scale_within_channel` min-maxes to ``[0, 1]`` so channels share a scale
before fusion (floors at 0 for ``route`` aggregation).

**The distinction :func:`fuse` needs and used not to have** is between a channel
that was never consulted for this query and one that was consulted and did not
score *this document*. It used to renormalise over the channels present in the
score dict, which conflates them, and the conflation is not neutral — it made
additional evidence lower a score. :func:`fuse` therefore takes the consulted
set, and a consulted channel absent from ``scores`` contributes 0.0.
"""


from __future__ import annotations

from collections.abc import Collection, Mapping

__all__ = ["fuse", "scale_within_channel"]


def scale_within_channel(scores: Mapping[str, float]) -> dict[str, float]:
    """Min-max channel scores to ``[0, 1]`` over documents scored (floors at 0 for ``route``).

    BM25.search still returns absolute saturated scores for the record; this scaling
    is what ``route`` sums so channels are commensurate.

    **Known residual, deliberately not changed here.** The floor is exactly 0.0, so whenever a
    channel scores two or more documents with distinct values, the weakest one is scaled to the
    same value a document that channel never saw would get. Since the vector store returns a
    cosine for every candidate, that is the normal case on the semantic side. It no longer
    causes the non-monotonicity :func:`fuse` documents, but it does still throw away the
    difference between "worst evidence" and "no evidence".

    Left alone because the min-max is the thing a measurement chose: BM25 after saturation runs
    0.60–0.97 and cosine runs 0.00–0.635, so a 0.5/0.5 blend of the raw values was the lexical
    score plus a small constant. Changing the normaliser needs a measurement of its own, and
    fixing the monotonicity did not.
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


def fuse(scores: Mapping[str, float], weights: Mapping[str, float], *,
         consulted: Collection[str]) -> float:
    """``sum(w_c * score_c) / sum(w_c)`` over ``consulted``, absent-but-consulted counted 0.0.

    ``consulted`` is the set of channels that ran for this query — a fact the caller has and
    this function cannot recover. Renormalising over it, rather than over the channels present
    in ``scores``, is what makes the result **monotone in evidence**.

    **The defect this signature exists to prevent.** The old form averaged over the channels
    present in ``scores``, so an absent channel was *neutral* while a present one scored 0.0
    was *maximally penalising* — and ``scale_within_channel`` floors each channel's weakest
    document at exactly 0.0. With the shipped weights (0.5/0.5):

        A: lexical 0.6, semantic never scored it   -> 0.6 / 1.0    = 0.60
        B: lexical 0.6, semantic scaled it to 0.0  -> 0.3 / 1.0    = 0.30

    B was found by **both** channels and scored half of A. Restated: an asset found by both
    channels could rank below an asset found by only one. Verified end to end through
    ``serve/nodes/facets.py`` with a real BM25 and a real index.

    Note that restoring a positive floor would *not* have fixed it. Any weighted **mean** over
    a varying set of terms falls when a term below the current mean is added, so with
    ``semantic 0.1`` the pair becomes 0.60 against 0.35 and B is still penalised for the extra
    evidence. The mean is the problem; the fixed denominator is the fix. Every term is now
    non-negative and the denominator does not depend on which channels found the document, so
    adding evidence can only raise the score.

    This is a different and more insidious defect than the ``max(lexical, semantic)`` bug fixed
    in ``5499ab2``: that one made the semantic channel lose every time, which is at least
    consistent.

    A channel in ``scores`` but not in ``consulted`` raises: it means the caller scored a
    document on a channel it says it did not run, and silently including it would put the
    denominator and the numerator over different channel sets.
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
        # A consulted channel that did not score this document scores it 0.0. That is the
        # correction: it is a *measurement* by that channel, not an absence of one.
        weighted += w * float(scores.get(channel, 0.0))
        active_weight += w
    if active_weight == 0.0:
        raise ValueError("fuse requires at least one consulted channel with non-zero weight")
    return weighted / active_weight
