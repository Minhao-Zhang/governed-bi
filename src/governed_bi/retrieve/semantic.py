"""Semantic channel: cosine similarity and embedding cache keys.

``cosine`` raises on a width mismatch — returning 0.0 here made a cross-model
cache hit look like irrelevance. Cache keys carry model and dimensions so two
embedders cannot share an entry by accident.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING

from governed_bi.register.facets import ChannelState

if TYPE_CHECKING:  # ``index`` imports ``cache_key`` from here, so this stays a hint only.
    from .index import UnifiedIndex

__all__ = ["cosine", "cache_key", "semantic_search"]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Standard cosine similarity. Raises if vector widths differ."""
    if len(a) != len(b):
        raise ValueError(
            f"cosine width mismatch: {len(a)} vs {len(b)}"
        )
    if not a:
        raise ValueError("cosine of empty vectors is undefined")

    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y

    denom = math.sqrt(na) * math.sqrt(nb)
    if denom == 0.0:
        raise ValueError("cosine of a zero vector is undefined")
    return dot / denom


def cache_key(text: str, *, model: str, dimensions: int) -> str:
    """Content key that includes embedder identity (model + dimensions)."""
    return f"{model}|{dimensions}|{text}"


def semantic_search(
    index: UnifiedIndex,
    query_vector: Sequence[float] | None,
    *,
    candidates: Collection[str] | None = None,
    top_n: int | None = None,
) -> tuple[list[tuple[str, float]], ChannelState]:
    """Score ``candidates`` by cosine against ``query_vector``. **The semantic channel.**

    Returns ``(ranked, state)``: ``(asset_id, cosine)`` best first, ties broken by id so
    two runs over one index cannot disagree, and the observed
    :class:`~governed_bi.register.facets.ChannelState`.

    **Why this exists rather than a per-document lookup at the call site.**
    ``register/facets.py:116`` gives ``Stage.facet_example`` only ``Channel.semantic``, so
    a few-shot has to be *findable* by cosine alone — not merely scored by it once
    something else has found it. A caller that ranks lexically and then attaches a cosine
    to the survivors has no semantic channel at all for that facet: it has a lexical
    channel the facet does not declare, wearing a cosine. Whether the few-shot came back
    then depends on term-frequency overlap between two natural-language questions, which
    is the matching ``docs/plans/v2-layer-handoffs.md`` §6 rules out *by design* because
    it rewards shared function words.

    **A candidate that ran and scored zero is returned, at zero.** ``ChannelState.ran``
    *"says nothing about whether it found anything — 'ran and scored zero' is a
    measurement, and not this field's job"*. Dropping the zeros here would make "the
    channel found nothing" and "the channel was never wired up" the same observation,
    which is the shape half this repository's retired numbers have. Callers threshold;
    this function measures.

    ``state`` is ``not_configured`` when there is nothing to measure with — no embedder
    built this index, or no query vector — and ``ran`` otherwise. It is never ``failed``:
    a rate limit or a dead endpoint raises out of the embedder (``ports.py:127``) before
    any vector reaches here, and it is the caller holding that ``try`` that knows a
    failure happened. A width mismatch likewise **raises** out of :func:`cosine` rather
    than being folded into a state, because returning 0.0 there is the v1 incident this
    module was rewritten around.
    """
    ids = (
        sorted(str(c) for c in candidates)
        if candidates is not None
        else sorted(index.entries)
    )

    if index.embedder_model is None or not index.vectors or query_vector is None:
        return [], ChannelState.not_configured

    scored: list[tuple[str, float]] = []
    for asset_id in ids:
        entry = index.entries.get(asset_id)
        if entry is None:
            continue
        doc_vector = index.vectors.get(entry.summary)
        if doc_vector is None:
            continue
        scored.append((asset_id, float(cosine(query_vector, doc_vector))))

    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    if top_n is not None:
        scored = scored[:max(0, int(top_n))]
    return scored, ChannelState.ran
