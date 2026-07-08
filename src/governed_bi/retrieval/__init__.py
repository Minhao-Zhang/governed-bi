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
"""
