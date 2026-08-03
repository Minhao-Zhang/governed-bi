"""Faceted retrieval: lexical + semantic scoring, route, closure, budgets.

Parcel E. Spec: ADR 0005 §2, ``docs/plans/v2-layer-handoffs.md`` §6.

``route`` is intentionally **not** re-exported as a function here — the scoring
contract imports ``governed_bi.retrieve.route`` as the submodule (to read its
source). Callers use ``from governed_bi.retrieve.route import route``.
"""

from __future__ import annotations

from . import route as route  # noqa: F401 — submodule must remain importable by name
from .budget import BudgetResult, apply_budgets, budget_for
from .connect import ConnectResult, canon_edge, connect
from .fuse import fuse
from .index import IndexEntry, UnifiedIndex, build_index, schema_tag_for
from .lexical import BM25
from .resolve import resolve
from .result import Hit
from .semantic import cache_key, cosine, semantic_search
from .structure import CorpusStructure, build_structure, complete_joins

__all__ = [
    "BM25",
    "BudgetResult",
    "ConnectResult",
    "CorpusStructure",
    "Hit",
    "IndexEntry",
    "UnifiedIndex",
    "apply_budgets",
    "budget_for",
    "build_index",
    "build_structure",
    "cache_key",
    "canon_edge",
    "complete_joins",
    "connect",
    "cosine",
    "fuse",
    "resolve",
    "route",
    "schema_tag_for",
    "semantic_search",
]
