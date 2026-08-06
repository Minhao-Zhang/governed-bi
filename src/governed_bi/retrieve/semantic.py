"""Semantic channel: cosine similarity and embedding cache keys.

``cosine`` raises on width mismatch. Cache keys carry model and dimensions.
Scoring runs in LanceDB; ``cosine`` is the reference definition for tests.
"""


from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING

from governed_bi.register.facets import ChannelState

if TYPE_CHECKING:  # ``index`` imports ``cache_key`` from here, so this stays a hint only.
    from .index import UnifiedIndex

__all__ = ["cosine", "cache_key", "check_query_vector", "semantic_search"]


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


def check_query_vector(vector: Sequence[float], *, width: int) -> None:
    """Refuse a query :func:`cosine` would refuse (wrong width, empty, or zero).

    LanceDB does not make these refusals; keep one definition shared with the store.
    """
    if len(vector) != width:
        raise ValueError(f"cosine width mismatch: {len(vector)} vs {width}")
    if not vector:
        raise ValueError("cosine of empty vectors is undefined")
    if not any(vector):
        raise ValueError("cosine of a zero vector is undefined")


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
    """Cosine search over ``candidates`` (or the whole index).

    Returns ``(hits, state)``. Zero scores are kept (measurement, not absence).
    ``not_configured`` when no embedder/store/query vector; otherwise ``ran``.
    Width mismatch and zero query raise. State decided in Python before the store.
    """
    ids = (
        sorted(str(c) for c in candidates)
        if candidates is not None
        else sorted(index.entries)
    )

    store = index.vectors
    if index.embedder_model is None or store is None or len(store) == 0 or query_vector is None:
        return [], ChannelState.not_configured

    # A candidate set that is empty **ran** — it is a measurement ("nothing of these types
    # is a candidate"), not a missing channel — and it must not reach `search`, whose
    # `limit` is mandatory and where zero is refused outright.
    if not ids:
        return [], ChannelState.ran

    scored = store.search(query_vector, keys=ids)
    # LanceDB orders by distance and breaks ties by insertion order. Re-sorted here so two
    # indexes built from the same assets in a different order cannot disagree.
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    if top_n is not None:
        scored = scored[:max(0, int(top_n))]
    return scored, ChannelState.ran
