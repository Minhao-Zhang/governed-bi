"""Cosine is the reference definition. Scoring runs in the store. See ADR 0011."""


from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING

from governed_bi.registry.channels import ChannelState

if TYPE_CHECKING:  # ``index`` imports ``cache_key`` from here, so this stays a hint only.
    from .index import UnifiedIndex

__all__ = ["cosine", "cache_key", "check_query_vector", "semantic_search"]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
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
    """The store shares this refusal. The database accepts a bad query vector."""
    if len(vector) != width:
        raise ValueError(f"cosine width mismatch: {len(vector)} vs {width}")
    if not vector:
        raise ValueError("cosine of empty vectors is undefined")
    if not any(vector):
        raise ValueError("cosine of a zero vector is undefined")


def cache_key(text: str, *, model: str, dimensions: int) -> str:
    return f"{model}|{dimensions}|{text}"


def semantic_search(
    index: UnifiedIndex,
    query_vector: Sequence[float] | None,
    *,
    candidates: Collection[str] | None = None,
    top_n: int | None = None,
) -> tuple[list[tuple[str, float]], ChannelState]:
    """Zero scores stay. State is chosen here, before the store."""
    ids = (
        sorted(str(c) for c in candidates)
        if candidates is not None
        else sorted(index.entries)
    )

    store = index.vectors
    if index.embedder_model is None or store is None or len(store) == 0 or query_vector is None:
        return [], ChannelState.not_configured

    # An empty candidate set still ran. ``search`` refuses a zero limit.
    if not ids:
        return [], ChannelState.ran

    scored = store.search(query_vector, keys=ids)
    # Ties follow insertion order. Sort so two build orders agree.
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    if top_n is not None:
        scored = scored[:max(0, int(top_n))]
    return scored, ChannelState.ran
