"""RVGD retrieval (Server step 5).

Four retrieval modes, four-stage rerank, token-budgeted, Corrective-RAG
fallback:

- **R** exact (id / physical-name lookup)
- **V** semantic (vector index)
- **G** graph (neighborhood over the projected FK graph)
- **D** dictionary (term / synonym resolution)

Retrieves the **Facts + Inference tiers only** (loader contract); Audit and
``governance.excluded`` assets are never retrieved. The vector / BM25 indexes
are rebuildable projections under ``corpus/_generated/``.

This slice ships the deterministic lexical (BM25) channel plus the Ground
expansion; see ``rvgd.py``. On the multi-schema path, ``schema_router`` shortlists
schemas and expands along curated joins before ``retrieve``. Semantic (V) fusion
is optional via an embedder; graph (G) and Corrective-RAG reranking are later slices.
"""

from __future__ import annotations

from .embedding import EmbeddingIndex, build_embedding_index, fuse_rankings
from .rvgd import (
    BM25Index,
    RetrievalResult,
    asset_document,
    build_index,
    retrieve,
    tokenize,
)
from .schema_router import (
    expand_schemas_via_curated_joins,
    filter_corpus_for_retrieval,
    route_schemas,
    select_schema,
    shortlist_schemas,
)

__all__ = [
    "BM25Index",
    "EmbeddingIndex",
    "RetrievalResult",
    "asset_document",
    "build_embedding_index",
    "build_index",
    "expand_schemas_via_curated_joins",
    "filter_corpus_for_retrieval",
    "fuse_rankings",
    "retrieve",
    "route_schemas",
    "select_schema",
    "shortlist_schemas",
    "tokenize",
]
