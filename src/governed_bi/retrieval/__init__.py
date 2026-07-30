"""RVGD retrieval (Analyst step 5).

Four retrieval modes, four-stage rerank, token-budgeted, Corrective-RAG
fallback:

RVGD names four **retrieval methods**, not four asset classes. Any asset type can
in principle be reached by more than one method, and a method's coverage is a
property of what we index, not of the asset:

- **R** exact (id / physical-name lookup, and exact hits on a term's synonyms)
- **V** semantic (dense vector index) and the lexical BM25 channel
- **G** graph (neighborhood over the projected FK graph)
- **D** dynamic few-shot (retrieve past question -> SQL pairs by similarity, and
  accumulate new ones from verified successes)

Coverage today: R and V ship. **G is not built**, which matters most for joins,
whose only natural method it is (``asset_document`` gives ``JoinAsset`` no
language surface, so R and V cannot reach it either; ``assemble_context``
therefore takes joins by licensed scope rather than by retrieval). **D is only
half-built**: few-shots are retrieved by V over their question text, but they are
authored at build time only, so nothing accumulates from a successful serve.

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
    RetrievalIndexCache,
    RetrievalResult,
    asset_document,
    build_index,
    corpus_index_key,
    retrieve,
    tokenize,
)
from .schema_router import (
    SCHEMA_PICK_MAX_TABLES,
    SchemaPick,
    embed_schema_documents,
    expand_schemas_via_curated_joins,
    filter_corpus_for_retrieval,
    pick_schema,
    route_schemas,
    shortlist_schemas,
)
from .triggers import fire_triggers

__all__ = [
    "BM25Index",
    "EmbeddingIndex",
    "RetrievalResult",
    "asset_document",
    "build_embedding_index",
    "build_index",
    "embed_schema_documents",
    "expand_schemas_via_curated_joins",
    "filter_corpus_for_retrieval",
    "fire_triggers",
    "fuse_rankings",
    "RetrievalIndexCache",
    "corpus_index_key",
    "retrieve",
    "route_schemas",
    "SCHEMA_PICK_MAX_TABLES",
    "SchemaPick",
    "pick_schema",
    "shortlist_schemas",
    "tokenize",
]
