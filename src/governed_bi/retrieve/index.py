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
    """Both channels over the same entry set. IDF is global; vectors are optional.

    **``vectors`` is keyed on asset id, and the cross-model cache key is not.** The two
    are different scopes and conflating them is what made v1's defect survive: one index
    is built by exactly one embedder, and :attr:`embedder_model` /
    :attr:`embedder_dimensions` pin which — so *inside* this object the id alone is an
    unambiguous key. The dangerous key is the one on a cache that **outlives one build**
    and is handed to the next embedder, which is :func:`build_index`'s ``vector_cache``
    parameter, and that one carries ``(model, dimensions, text)`` via
    :func:`~governed_bi.retrieve.semantic.cache_key`.

    The id keying is also what lets the candidate restriction be a predicate LanceDB can
    evaluate: keyed on summary text, narrowing to a candidate set would mean shipping
    every candidate's summary into a SQL ``IN``.

    There is no ``restrict_to`` here. There was one, it kept the shared BM25 postings so
    global IDF survived a narrowing, and **nothing ever called it** — pass two narrows
    ``BM25`` directly (``pass_two.py:48``). A declared-but-unwired method is a defect in
    this repository, and porting one to a new storage layer is how it acquires a second
    life; it was deleted rather than rewritten.
    """

    entries: Mapping[str, IndexEntry]
    lexical: BM25
    #: This index's vectors, keyed on asset id. ``None`` until an embedder runs — ``None``
    #: and not an empty store, so "nobody configured an embedder" stays a different fact
    #: from "an embedder ran and produced nothing". Read it only together with
    #: :attr:`embedder_model` — a vector whose embedder identity is unknown is the input to
    #: a cross-model cosine, and cosine cannot detect one when the widths agree.
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

    Blank summaries are refused (I1 / §1.1). Lexical is always built. When ``embedder``
    is set, each summary is looked up in ``vector_cache`` and the misses are embedded
    once, in one batch, and written back.

    **``vector_cache`` is keyed by :func:`~governed_bi.retrieve.semantic.cache_key`, not
    by summary text.** It was keyed on the text alone until 2026-08-03, which contradicted
    ``register/knobs.py:208`` — *"[embedding_model is] part of every vector cache key"* —
    in the one shape the existing backstop cannot reach. ``semantic.cosine`` raises when
    two vectors' widths differ, and that closes the case the widths *differ*;
    ``text-embedding-3-large`` accepts a ``dimensions`` argument, so a 1536-wide 3-large
    and a 1536-wide 3-small are **width-identical and semantically unrelated**, and a
    text-keyed cache hands one model's vector to the other with nothing anywhere
    disagreeing. That is v1's cache defect — a cross-model hit degrading routing to
    "nothing scores" with no error — surviving the fix that was supposed to end it.

    The signature change is the fix rather than a caller convention, because *"a cache
    that is correct only when the caller remembers to keep one dict per model"* is the
    convention this port exists to replace. One dict is now safe to share across every
    embedder in a pooled run, which is also what makes it useful: misses are written back
    into it, so the second build with the same embedder is a hit and the second build
    with a **different** one is a miss.

    ``vector_cache`` without ``embedder`` raises. The key needs a model and a width, and
    with no embedder there is nothing to take them from — reading such a cache would mean
    guessing whose vectors it holds, which is the guess this parameter was reshaped to
    delete. Pass the embedder even when you expect every lookup to hit.

    **The cache is a store and not a mapping, and the index's vectors are copied out of it
    in Arrow.** It was ``MutableMapping[str, Vector]`` and the only persistent
    implementation was an 848 MB JSON file that took 21.7 s to parse at every boot and then
    sat in memory twice, because this function turned it into a second dict of the same
    Python floats. ``retrieve/vectors.py`` records the measurement. When no cache is
    supplied an ephemeral one is opened rather than branching, so a build with a cache and
    a build without run the identical code path — two paths here is how the cached and
    uncached builds would come to disagree about what is in the index.
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
