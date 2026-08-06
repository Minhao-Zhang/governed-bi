"""Weighted hybrid fusion, renormalised by active channels.

A channel absent from ``scores`` did not run — it is skipped, not treated as
0.0. That distinction is load-bearing: scoring zero is evidence of irrelevance;
not running is no evidence at all.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["fuse", "scale_within_channel"]


def scale_within_channel(scores: Mapping[str, float]) -> dict[str, float]:
    """One channel's scores, min-maxed to ``[0, 1]`` over the documents it actually scored.

    **:func:`fuse` cannot do its job on inputs that do not share a scale, and for a year they
    did not.** BM25-after-saturation occupies roughly 0.60–0.97 for anything surviving the depth
    cut while cosine caps around 0.635, so a 0.5/0.5 blend of the raw values is not a blend: it
    is BM25 plus a small constant. Over 32 244 documents that both channels scored, the semantic
    channel never once ranked above the lexical one.

    Measured on 342 held-out questions, identical retrieval evidence, only this scaling varying
    (schema recall@3 / gold tables inside the 8-table budget):

    ==========================  =========  ==========
    rule                        recall@3   tables@8
    ==========================  =========  ==========
    lexical channel alone          0.7018      0.2219
    raw ``max`` (shipped)          0.9269      0.4084
    scaled                         0.9620      0.6559
    semantic channel alone         0.9825      0.6688
    ==========================  =========  ==========

    It is also what makes the tokenizer repair safe: with punctuation stripped BM25 gets
    stronger, and on raw scales a stronger wrong-scaled channel makes the system *worse*
    (recall@3 0.9269 → 0.8947). Scaled, the same repair is a gain.

    **Min-max and not a z-score, because of how ``route`` aggregates.** ``route`` sums each
    facet's best score per schema, so a negative contribution would rank "found, below this
    facet's average" beneath "not found at all" — the absence-as-evidence mistake this module's
    own docstring forbids, one level up. Min-max floors at 0, so a weak hit is worth what an
    absent one is worth and never less.

    **What this trades away, stated rather than left to be found.**
    ``retrieve/lexical.py`` and ``tests/retrieve/test_scoring_contract.py`` require BM25's own
    score to be absolute rather than relative to the current query's best hit, precisely so it
    can be summed across facets. That contract is untouched — ``BM25.search`` still returns
    absolute saturated scores and the raw value is still what the record publishes. What becomes
    query-relative is the number ``route`` sums. That is a real cost, and it is worth paying
    because the "absolute" scores were never commensurable: besides the channel gap above, the
    corpus-global ``avgdl`` of 8.32 tokens is set by 5 947 three-token column summaries, which
    hands each facet a fixed multiplicative offset — one term match is worth 1.70× more in a
    column summary than in a term or few-shot summary.
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
