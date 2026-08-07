"""Unified retrieval index over ``summary`` only (ADR 0005 §2.2).

One entry per asset; the indexed text is that asset's ``summary``. Schema tags
are computed at build (or accepted precomputed) so ``route`` can aggregate
before ``resolve``. Lexical postings come from :class:`.lexical.BM25` — that
module must exist. Semantic vectors live in a :class:`~.vectors.VectorStore`
when an embedder is supplied; this module does not call a provider SDK.

Requires ``retrieve.lexical.BM25``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Sequence

from governed_bi.ports import Embedder
from governed_bi.register.assets import ASSET_REGISTER, AssetType, TagRule
from governed_bi.register.knobs import knob_default

from .lexical import BM25
from .semantic import cache_key

if TYPE_CHECKING:
    # Imported for real inside ``build_index``, and only when an embedder is passed.
    # ``import lancedb`` costs ~1.1 s, which every no-embedder caller — 50 of the 55 index
    # builds in the test suite, and every ``langgraph dev`` reload — would otherwise pay
    # for a store it never opens.
    from .vector_cache import VectorCache
    from .vectors import VectorStore

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
    """Both channels over the same entry set. IDF is global; vectors optional.

    ``vectors`` is keyed on asset id (safe within one build). The persistent
    ``vector_cache`` key carries ``(model, dimensions, text)`` via
    :func:`~governed_bi.retrieve.semantic.cache_key`.
    """

    entries: Mapping[str, IndexEntry]
    lexical: BM25
    #: This index's vectors by asset id. ``None`` means no embedder was configured.
    vectors: VectorStore | None
    embedder_model: str | None = None
    embedder_dimensions: int | None = None


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
    vector_cache: VectorCache | None = None,
) -> UnifiedIndex:
    """Build the unified index from summary-bearing entries.

    Blank summaries refused (I1). Lexical always built. With ``embedder``, look up
    ``vector_cache`` by :func:`~.semantic.cache_key` (model + dimensions + text),
    embed misses once, write back. Cache without embedder raises. Vectors copied
    from the store in Arrow; ephemeral store if no cache supplied.
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

    # `k` from the register, not from `BM25`'s own default. The two agreed at 1.2, which is
    # exactly why nobody noticed that `lexical_saturation_k` shipped UNSET while this line ran
    # a literal: the record omitted the knob and the code chose the value.
    lexical = BM25(docs, k=float(knob_default("lexical_saturation_k")))

    if vector_cache is not None and embedder is None:
        raise ValueError(
            "vector_cache requires an embedder: the key is (model, dimensions, text), "
            "so without one there is no identity to read the cache under "
            "(register/knobs.py:208)"
        )

    vectors: VectorStore | None = None
    model: str | None = None
    dims: int | None = None
    if embedder is not None:
        from .vector_cache import VectorCache  # see the TYPE_CHECKING note above
        from .vectors import VectorStore

        model, dims = embedder.model, embedder.dimensions
        # The cache holds one store per width and this build reads exactly one of them. A
        # wrong-width row cannot reach it: the column type is `fixed_size_list[dims]`, so
        # the storage layer refuses what the dict cache needed a hand-written check for.
        cached = (VectorCache() if vector_cache is None else vector_cache).at_width(dims)

        # One cache key per distinct summary — two assets sharing a summary share the
        # entry, which is the whole reason the cache is content-keyed and not id-keyed:
        # curation rewrites summaries in place under the same id (ADR 0005 §2.2).
        keys: dict[str, str] = {}
        for entry in by_id.values():
            keys.setdefault(entry.summary, cache_key(entry.summary, model=model, dimensions=dims))

        absent = set(cached.missing(list(keys.values())))
        missing = [text for text, key in keys.items() if key in absent]
        if missing:
            embedded = embedder.embed(missing)
            if len(embedded) != len(missing):
                raise ValueError("embedder returned the wrong number of vectors")
            cached.add({keys[text]: vec for text, vec in zip(missing, embedded, strict=True)})

        vectors = VectorStore(dims)
        vectors.load_from(cached, [(keys[e.summary], e.id) for e in by_id.values()])

    return UnifiedIndex(
        entries=by_id,
        lexical=lexical,
        vectors=vectors,
        embedder_model=model,
        embedder_dimensions=dims,
    )
