"""The V (vector / semantic) channel of RVGD, plus rank fusion.

BM25 (``rvgd.py``) is a lexical channel: it matches on shared tokens, so it under-
recalls paraphrases ("earnings" vs "revenue"). The vector channel embeds each
asset's document and the question with an :class:`~governed_bi.llm.Embedder` and
ranks by cosine, catching semantic matches BM25 misses. The two rankings are
combined with **Reciprocal Rank Fusion** (RRF), which needs no score
normalization across the two very different score scales.

The embedder is injected (OpenAI in production, the deterministic
:class:`~governed_bi.llm.HashingEmbedder` offline), so this module needs no
network and no key. The index is a rebuildable projection, rebuilt per call like
the BM25 index; for the small search spaces here a brute-force cosine scan is
enough (no ANN index).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..llm import cosine
from .rvgd import asset_document

if TYPE_CHECKING:
    from ..corpus import Corpus
    from ..llm import Embedder


@dataclass
class EmbeddingIndex:
    """A brute-force cosine index over ``asset_id -> vector``."""

    vectors: dict[str, list[float]]

    def rank(self, query_vector: list[float]) -> list[tuple[str, float]]:
        """Score every asset against the query vector; return the > 0 matches,
        ordered by cosine descending then id ascending (deterministic)."""
        scored = [(doc_id, cosine(query_vector, vec)) for doc_id, vec in self.vectors.items()]
        scored = [(doc_id, s) for doc_id, s in scored if s > 0.0]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored


def build_embedding_index(corpus: "Corpus", embedder: "Embedder") -> EmbeddingIndex:
    """Embed one document per asset (the same text BM25 indexes) into an index."""
    ids = [a.id for a in corpus.assets]
    docs = [asset_document(a) for a in corpus.assets]
    vectors = embedder.embed(docs)
    return EmbeddingIndex(dict(zip(ids, vectors)))


def fuse_rankings(
    *rankings: list[tuple[str, float]],
    k: int = 60,
    weights: list[float] | None = None,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion of one or more ranked ``(id, score)`` lists.

    Each list contributes ``weight * 1 / (k + rank)`` (1-based rank) per id;
    contributions sum across lists. The raw per-channel scores are ignored (only
    positions matter), so lexical BM25 scores and cosine similarities combine
    without any normalization. Ties break by id ascending, so the result is
    deterministic. ``k=60`` is the conventional RRF constant.

    ``weights`` (one per ranking, defaulting to all ``1.0``) tunes the channels'
    relative pull — e.g. ``weights=[1.0, 0.5]`` trusts lexical over semantic. It
    scales only the RRF contributions, so fusion stays normalization-free.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError(f"weights ({len(weights)}) must match rankings ({len(rankings)})")
    scores: dict[str, float] = {}
    for weight, ranking in zip(weights, rankings):
        for position, (doc_id, _score) in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight * (1.0 / (k + position + 1))
    fused = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    return fused
