"""Semantic channel: cosine similarity and embedding cache keys.

``cosine`` raises on a width mismatch — returning 0.0 here made a cross-model
cache hit look like irrelevance. Cache keys carry model and dimensions so two
embedders cannot share an entry by accident.

Since 2026-08-04 the scoring runs inside LanceDB (``retrieve/vectors.py``) rather
than over a Python dict. ``cosine`` stays here, pure and exported, as the
**reference definition** the store is tested against — ``tests/retrieve/
test_vector_store.py`` fails if LanceDB's ranking or its distance conversion ever
diverges from it, and ``tests/retrieve/test_scoring_contract.py`` (a sealed file)
calls it on bare lists with no store anywhere. :func:`check_query_vector` is the
other half: the two refusals ``cosine`` makes, re-made at the store boundary,
because LanceDB makes neither.
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
    """Refuse a query :func:`cosine` would refuse, before a vector store swallows it.

    Both refusals are here because **LanceDB makes neither**, and each failure it
    substitutes is the absence-reads-as-zero shape this repository retires numbers over:

    * a query of the wrong width raises out of ``search`` only when the vector column is
      named explicitly — otherwise the error is *"There is no vector column in the data"*,
      which says nothing about width, and that is the v1 incident wearing a new message;
    * a **zero** query vector returns ``[]``. Not an error, not a NaN — an empty result
      indistinguishable from "no candidate matched", where ``cosine`` says *"cosine of a
      zero vector is undefined"* and ``model/deterministic_embedder.py`` keeps a zero-norm
      fallback precisely because it says so.

    One definition of "unusable for cosine", stated once, so the store and the pure
    function cannot drift apart.
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
    failure happened. A width mismatch likewise **raises** — out of
    :func:`check_query_vector` at the store boundary now that the scoring is LanceDB's —
    rather than being folded into a state, because returning 0.0 there is the v1 incident
    this module was rewritten around.

    **Every state is decided here, in Python, before the store is asked anything.** An
    empty candidate set, a filter that matched nothing, an empty table and a zero query
    vector all come back from LanceDB as exactly ``[]``; it cannot keep them apart, and
    invariant 5 says they are four different facts. So "no vectors" and "no query vector"
    are read off the index, "no candidates" short-circuits, and only "ran and scored
    something, or ran and scored nothing" reaches the store.
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
