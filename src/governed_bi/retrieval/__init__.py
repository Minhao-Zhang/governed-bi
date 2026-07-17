"""RVGD retrieval (Analyst step 5).

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
expansion; see ``rvgd.py``. The lexical channel has a **field-weight seam** to
lean matching onto the curated semantics (a table's description / grain, a
column's description) over the raw physical identifiers — held flat for now
(``_SEMANTIC_BOOST=1``) and left as a production-tuning knob (see ``rvgd.py``
TUNING). It tokenizes camelCase and stems simple plurals, and keeps matches under
**per-type budgets** so tables are never crowded out by a flood of matching
few-shots. Ground expansion also pulls in the
tables a retrieved few-shot's gold SQL references, and curator ``confidence`` is a
mild tie-breaker. On the multi-schema path, ``schema_router`` shortlists schemas
(single-pass docs, batched embeddings) and expands along curated joins before
``retrieve``. Semantic (V) fusion is optional via an embedder and its pull is
tunable (``vector_weight``); graph (G) and Corrective-RAG reranking are later slices.

Retrieval quality is measurable offline with ``eval/retrieval_eval.py`` (table
recall@k over gold SQL, no LLM): ``python -m governed_bi.eval.retrieval_eval``.
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
