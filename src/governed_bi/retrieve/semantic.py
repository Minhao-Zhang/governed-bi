"""Semantic channel: cosine similarity and embedding cache keys.

``cosine`` raises on a width mismatch — returning 0.0 here made a cross-model
cache hit look like irrelevance. Cache keys carry model and dimensions so two
embedders cannot share an entry by accident.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

__all__ = ["cosine", "cache_key"]


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
