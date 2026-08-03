"""Unified retrieval index over ``summary`` only (ADR 0005 §2.2).

One entry per asset; the indexed text is that asset's ``summary``. Schema tags
are computed at build (or accepted precomputed) so ``route`` can aggregate
before ``resolve``. Lexical postings come from :class:`.lexical.BM25` — that
module must exist. Semantic vectors are held by content key when an embedder
or cache is supplied; this module does not call a provider SDK.

Requires ``retrieve.lexical.BM25``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from governed_bi.ports import Embedder, Vector
from governed_bi.register.assets import ASSET_REGISTER, AssetType, TagRule

from .lexical import BM25

__all__ = [
    "IndexEntry",
    "UnifiedIndex",
    "build_index",
    "schema_tag_for",
]


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One indexed asset. ``summary`` is the only text that enters either channel."""

    id: str
    summary: str
    asset_type: AssetType
    #: Schema this entry votes for in ``route``. ``None`` = untagged (ADR §2.2).
    schema_tag: str | None = None


@dataclass(frozen=True, slots=True)
class UnifiedIndex:
    """Both channels over the same entry set. IDF is global; vectors are optional."""

    entries: Mapping[str, IndexEntry]
    lexical: BM25
    #: Content-keyed vectors (summary text → vector). Empty until an embedder runs.
    vectors: Mapping[str, Vector]
    embedder_model: str | None = None
    embedder_dimensions: int | None = None

    def restrict_to(self, ids: set[str]) -> UnifiedIndex:
        """Candidate filter that keeps the shared BM25 postings (global IDF)."""
        subset = {i: e for i, e in self.entries.items() if i in ids}
        return UnifiedIndex(
            entries=subset,
            lexical=self.lexical.restrict_to(ids),
            vectors={t: v for t, v in self.vectors.items() if any(
                e.summary == t for e in subset.values()
            )},
            embedder_model=self.embedder_model,
            embedder_dimensions=self.embedder_dimensions,
        )


def schema_tag_for(
    asset_type: AssetType,
    *,
    name: str | None = None,
    schema: str | None = None,
    parent_schema: str | None = None,
    base_table_schema: str | None = None,
    binding_schema: str | None = None,
    left_table_schema: str | None = None,
) -> str | None:
    """Derive the index-time schema tag from :class:`~governed_bi.register.assets.TagRule`.

    Callers that already computed the tag may pass it on :class:`IndexEntry`
    directly; this helper is the declared table in function form.
    """
    rule = ASSET_REGISTER[asset_type].tag_rule
    if rule is TagRule.itself:
        return name
    if rule is TagRule.own_schema:
        return schema
    if rule is TagRule.parent_table:
        return parent_schema
    if rule is TagRule.base_table:
        return base_table_schema
    if rule is TagRule.binding_target:
        return binding_schema  # unbound → None (untagged)
    if rule is TagRule.left_table:
        return left_table_schema
    if rule is TagRule.own_schema_or_global:
        return schema  # absent → untagged / system-wide
    raise ValueError(f"unknown tag rule: {rule!r}")


def build_index(
    entries: Sequence[IndexEntry],
    *,
    embedder: Embedder | None = None,
    vector_cache: Mapping[str, Vector] | None = None,
) -> UnifiedIndex:
    """Build the unified index from summary-bearing entries.

    Blank summaries are refused (I1 / §1.1). Lexical is always built. Vectors are
    taken from ``vector_cache`` (content-keyed) and, when ``embedder`` is set,
    any remaining summaries are embedded once and merged in.
    """
    by_id: dict[str, IndexEntry] = {}
    docs: list[tuple[str, str]] = []
    for entry in entries:
        text = entry.summary.strip()
        if not text:
            raise ValueError(f"refusing blank summary for {entry.id!r}")
        if entry.id in by_id:
            raise ValueError(f"duplicate index id: {entry.id!r}")
        by_id[entry.id] = entry
        docs.append((entry.id, text))

    lexical = BM25(docs)

    vectors: dict[str, Vector] = dict(vector_cache) if vector_cache else {}
    model: str | None = None
    dims: int | None = None
    if embedder is not None:
        model, dims = embedder.model, embedder.dimensions
        missing = [e.summary for e in by_id.values() if e.summary not in vectors]
        if missing:
            embedded = embedder.embed(missing)
            if len(embedded) != len(missing):
                raise ValueError("embedder returned the wrong number of vectors")
            for text, vec in zip(missing, embedded, strict=True):
                vectors[text] = vec

    return UnifiedIndex(
        entries=by_id,
        lexical=lexical,
        vectors=vectors,
        embedder_model=model,
        embedder_dimensions=dims,
    )
