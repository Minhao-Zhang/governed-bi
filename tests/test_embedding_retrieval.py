"""Tests for the V (vector) retrieval channel and RRF fusion.

Uses the deterministic HashingEmbedder, so no network. It has no real semantic
understanding (it is a lexical stand-in), so these tests assert structure,
determinism, and the BM25-only regression - not paraphrase recall, which needs
the real OpenAI embedder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.corpus import load_corpus
from governed_bi.llm import HashingEmbedder
from governed_bi.retrieval import (
    build_embedding_index,
    fuse_rankings,
    retrieve,
)
from governed_bi.retrieval.rvgd import asset_document

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


# --------------------------------------------------------------------------- #
# EmbeddingIndex
# --------------------------------------------------------------------------- #


def test_embedding_index_covers_every_asset_with_a_language_surface(corpus):
    """Every asset that has text gets a vector — and only those.

    ``asset_document`` returns "" by design for types with no language surface
    (joins), which it documents as "never match". This asserted equality with *all*
    assets, which quietly required the opposite: a vector for a blank document.
    """
    index = build_embedding_index(corpus, HashingEmbedder())
    expected = {a.id for a in corpus.assets if asset_document(a).strip()}
    assert set(index.vectors) == expected
    assert expected, "fixture corpus should have some embeddable assets"


def test_embedding_index_never_embeds_a_blank_document(corpus):
    """Regression: blank documents must not reach the embedder at all.

    Bedrock Titan rejects an empty ``inputText`` with
    ``ValidationException: expected minLength: 1``, which took down every chat turn
    on a join-heavy corpus. OpenAI accepts it, so the bug was invisible until the
    provider changed. Assert on what is *sent*, not just on the resulting index —
    the crash happened inside the embedder call.
    """
    seen: list[str] = []

    class RecordingStrictEmbedder(HashingEmbedder):
        def embed(self, texts):
            seen.extend(texts)
            if any(not t.strip() for t in texts):
                raise AssertionError("blank document sent to the embedder")
            return super().embed(texts)

    index = build_embedding_index(corpus, RecordingStrictEmbedder())
    assert seen, "embedder should have been called"
    # Joins are the blank-document type in this corpus; none may be indexed.
    assert not [asset_id for asset_id in index.vectors if asset_id.startswith("join_")]


def test_embedding_index_keeps_ids_aligned_with_vectors(corpus):
    """Skipping blanks must not shift the id->vector zip.

    Each id must map to the vector of *its own* document, so embedding a document
    directly has to reproduce the indexed vector.
    """
    embedder = HashingEmbedder(dimensions=128)
    index = build_embedding_index(corpus, embedder)
    by_id = {a.id: a for a in corpus.assets}
    for asset_id, vector in index.vectors.items():
        assert vector == embedder.embed_one(asset_document(by_id[asset_id]))


def test_embedding_index_rank_is_deterministic_and_sorted(corpus):
    index = build_embedding_index(corpus, HashingEmbedder(dimensions=256))
    emb = HashingEmbedder(dimensions=256)
    q = emb.embed_one("total revenue")
    r1 = index.rank(q)
    r2 = index.rank(q)
    assert r1 == r2  # deterministic
    scores = [s for _, s in r1]
    assert scores == sorted(scores, reverse=True)  # descending


def test_embedding_index_ranks_relevant_asset_up(corpus):
    index = build_embedding_index(corpus, HashingEmbedder(dimensions=512))
    emb = HashingEmbedder(dimensions=512)
    ranked = index.rank(emb.embed_one("revenue"))
    ids = [i for i, _ in ranked]
    # The revenue term/metric share the token "revenue", so they surface.
    assert "term_revenue" in ids or "metric_revenue" in ids


# --------------------------------------------------------------------------- #
# fuse_rankings (RRF)
# --------------------------------------------------------------------------- #


def test_fuse_rankings_math():
    a = [("x", 9.0), ("y", 1.0)]  # x rank1, y rank2
    b = [("y", 5.0), ("z", 2.0)]  # y rank1, z rank2
    fused = dict(fuse_rankings(a, b, k=60))
    assert fused["x"] == pytest.approx(1 / 61)
    assert fused["y"] == pytest.approx(1 / 62 + 1 / 61)  # in both lists
    assert fused["z"] == pytest.approx(1 / 62)


def test_fuse_rankings_orders_shared_hits_first():
    a = [("x", 9.0), ("y", 1.0)]
    b = [("y", 5.0), ("z", 2.0)]
    order = [i for i, _ in fuse_rankings(a, b)]
    assert order[0] == "y"  # appears in both -> highest fused score


def test_fuse_rankings_empty():
    assert fuse_rankings([], []) == []


# --------------------------------------------------------------------------- #
# retrieve() with and without the embedder
# --------------------------------------------------------------------------- #


def test_retrieve_without_embedder_is_pure_bm25(corpus):
    # The embedder default must not change existing behavior.
    base = retrieve(corpus, "total revenue")
    same = retrieve(corpus, "total revenue", embedder=None)
    assert base == same


def test_hybrid_retrieve_keeps_the_core_hits(corpus):
    # Fusion must not lose the strong BM25 hits for a clear question.
    hybrid = retrieve(corpus, "total revenue", embedder=HashingEmbedder(dimensions=512))
    assert "tbl_beer_factory_transaction" in hybrid.table_ids
    assert "metric_revenue" in hybrid.metric_ids


def test_hybrid_retrieve_is_deterministic(corpus):
    emb = HashingEmbedder(dimensions=512)
    r1 = retrieve(corpus, "revenue by brand", embedder=emb)
    r2 = retrieve(corpus, "revenue by brand", embedder=HashingEmbedder(dimensions=512))
    assert r1 == r2
