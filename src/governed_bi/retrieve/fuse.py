"""Weighted hybrid fusion over the channels that were **consulted**.

:func:`scale_to_ceiling` puts a channel on ``[0, 1]`` with a **fixed** ceiling, so
two channels are commensurate without either of them becoming relative to the
current query. The ``max(lexical, semantic)`` rule this ultimately replaced (fixed
in ``5499ab2``) compared a raw BM25 score against a raw cosine, on incomparable
scales, so the semantic channel lost essentially always.

:func:`fuse` takes the consulted set, because a channel never consulted and a
channel that scored this document zero are different facts; conflating them made
additional evidence lower a score. A consulted channel absent from ``scores``
contributes 0.0.
"""


from __future__ import annotations

from collections.abc import Collection, Mapping

__all__ = ["fuse", "scale_to_ceiling"]


def scale_to_ceiling(value: float, *, ceiling: float) -> float:
    """``value / ceiling``, clamped to ``[0, 1]``. A fixed map, not a per-query one.

    **This replaced a min-max over the facet's own scored population** (audit I1), which had
    three defects that were one defect: the top-scoring document became exactly 1.0, the weakest
    became exactly 0.0, and with a single scored document — or an all-tie — *every* document
    became 1.0. So each facet awarded a maximal vote to its own favourite whatever that
    favourite's absolute strength was, and ``route`` then **sums those votes across facets**: a
    facet that found nothing convincing voted exactly as loudly as one that found the right
    table. ``tests/retrieve/test_scoring_contract.py`` states the property being violated in its
    own words — "a score divided by the current query's best hit is only comparable *within* one
    query, so it cannot be summed across facets in ``route``" — and it tests ``BM25.search``,
    which is absolute, one layer below where the division was happening.

    **Reciprocal-rank fusion was the plan and is not the fix.** RRF is immune to *scale*, but it
    is purely ordinal: the top-ranked document scores ``1/(k+1)``, the maximum a channel can
    award, however weak the match. That is the same defect expressed in ranks.

    A fixed ceiling keeps what min-max destroyed — how good the best match actually was — and it
    needs no population, so the two retrieval passes cannot disagree about the scale by scoring
    different candidate sets.
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
