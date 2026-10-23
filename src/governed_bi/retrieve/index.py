"""One entry per asset; the indexed text is ``summary``. Schema tags are fixed at build. See ADR 0005."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Sequence

from governed_bi.ports import Embedder
from governed_bi.registry.assets import ASSET_REGISTER, AssetType, TagRule
from governed_bi.registry.settings import setting_default

from .lexical import BM25
from .semantic import cache_key

if TYPE_CHECKING:
    # Imported inside ``build_index`` only when an embedder is passed.
    # Importing the store costs about a second that no-embedder callers would pay.
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
    """``summary`` is the only text either channel indexes."""

    id: str
    summary: str
    asset_type: AssetType
    #: ``None`` means this entry does not vote in ``route``.
    schema_tag: str | None = None


@dataclass(frozen=True, slots=True)
class UnifiedIndex:
    entries: Mapping[str, IndexEntry]
    lexical: BM25
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
    raise ValueError(f"unknown tag rule: {rule!r}")


#: Not a setting. A config that picks this number is choosing not to be checked.
#: Same-model repeats agree past 0.9999; different models in one width do not.
_SAME_SPACE_COSINE = 0.99

#: Three probes, at the ends and the middle. One probe can land on a new-space row.
#: This sample catches a whole-store move. A tiny partial re-embed can still slip through.
_SPACE_PROBES = 3

#: Fixed text, content-keyed like every other row, so the check needs no schema change.
_SPACE_CANARY_TEXT = "governed-bi vector space canary"

def _refuse_a_mixed_vector_space(
    cached: VectorStore,
    keys: Mapping[str, str],
    absent: set[str],
    *,
    embedder: Embedder,
) -> None:
    """Refuse rows from another embedder behind the same model id.

    The key is model, dimensions, and text, so a repointed gateway still hits. A canary row names the space that wrote
    the store. A same-process repoint that hits every row stays unchecked until the next process opens the store.
    """
    reused = sorted(text for text, key in keys.items() if key not in absent)
    if cached.opened_with == 0:
        # A same-id repoint is not a different cache key. Probe a reused row when
        # this build also has misses. No canary here: a cold store may not store
        # an extra row, and a full hit has no new vector to compare.
        if reused and absent:
            probe = [reused[0]]
            fresh = embedder.embed(probe)
            if len(fresh) != len(probe):
                raise ValueError("embedder returned the wrong number of vectors")
            _assert_one_space(
                cached, keys[probe[0]], fresh[0], embedder=embedder,
                what="a summary this process embedded earlier",
            )
        return
    identity = (str(embedder.model), int(embedder.dimensions))
    if cached.space_is_verified(identity):
        return

    canary_key = cache_key(
        _SPACE_CANARY_TEXT, model=str(embedder.model), dimensions=int(embedder.dimensions)
    )
    minting = bool(cached.missing([canary_key]))

    to_embed = [_SPACE_CANARY_TEXT]
    probes: list[str] = []
    if minting:
        # Sample the store's own rows, not this build's reused set. A build that
        # reuses nothing would otherwise mint the canary having examined none.
        # Only this embedder's prefix: another model's rows are a different space.
        prefix = f"{embedder.model}|{int(embedder.dimensions)}|"
        mine = sorted(k for k in cached.keys() if k.startswith(prefix) and k != canary_key)
        if mine:
            n = len(mine)
            chosen = list(dict.fromkeys([mine[0], mine[n // 2], mine[n - 1]]))[:_SPACE_PROBES]
            probes = [k[len(prefix):] for k in chosen]
            to_embed += probes

    fresh = embedder.embed(to_embed)
    if len(fresh) != len(to_embed):
        raise ValueError("embedder returned the wrong number of vectors")
    canary_vector, probe_vectors = fresh[0], fresh[1:]

    for text, vector in zip(probes, probe_vectors, strict=True):
        _assert_one_space(
            cached,
            cache_key(text, model=str(embedder.model), dimensions=int(embedder.dimensions)),
            vector,
            embedder=embedder,
            what="a cached summary",
        )

    if minting:
        cached.add({canary_key: canary_vector})
    else:
        _assert_one_space(
            cached, canary_key, canary_vector, embedder=embedder, what="the canary row"
        )
    cached.mark_space_verified(identity)


def _assert_one_space(
    cached: VectorStore,
    key: str,
    fresh: Sequence[float],
    *,
    embedder: Embedder,
    what: str,
) -> None:
    similarity = dict(cached.search(list(fresh), keys=[key])).get(key)
    if similarity is None:
        # Present a moment ago. A row that disappears between the two reads is not a store to build on.
        raise ValueError(f"cached vector for {key[:80]!r}... vanished between `missing` and `search`")
    if similarity < _SAME_SPACE_COSINE:
        raise ValueError(
            f"the vector cache at {cached.uri} holds rows from a different embedding space: "
            f"re-embedding {what} with {embedder.model!r} at {embedder.dimensions} dimensions "
            f"agrees with the stored row at cosine {similarity!r}, below {_SAME_SPACE_COSINE}. "
            "The cache key is model|dimensions|text, so a gateway repointed at another model "
            "behind the same id hits on every old row — one index, two spaces, and cosine "
            "between them is noise. Delete the store for this model or point the cache "
            "elsewhere; do not mix them."
        )


def build_index(
    entries: Sequence[IndexEntry],
    *,
    embedder: Embedder | None = None,
    vector_cache: VectorCache | None = None,
) -> UnifiedIndex:
    by_id: dict[str, IndexEntry] = {}
    docs: list[tuple[str, str]] = []
    # One string for both channels. Strip on one side and not the other made
    # them score different text, and a whitespace-only edit re-embedded an
    # asset whose lexical postings had not changed.
    indexed_text: dict[str, str] = {}
    for entry in entries:
        text = entry.summary.strip()
        if not text:
            raise ValueError(f"refusing blank summary for {entry.id!r}")
        if entry.id in by_id:
            raise ValueError(f"duplicate index id: {entry.id!r}")
        by_id[entry.id] = entry
        indexed_text[entry.id] = text
        docs.append((entry.id, text))

    # ``k`` comes from the register. A literal here can drift from ``lexical_saturation_k``.
    lexical = BM25(docs, k=float(setting_default("lexical_saturation_k")))

    if vector_cache is not None and embedder is None:
        raise ValueError(
            "vector_cache requires an embedder: the key is (model, dimensions, text), "
            "so without one there is no identity to read the cache under "
            "(registry/settings.py, the embedding_model setting)"
        )

    vectors: VectorStore | None = None
    model: str | None = None
    dims: int | None = None
    if embedder is not None:
        from .vector_cache import VectorCache  # see the TYPE_CHECKING note above
        from .vectors import VectorStore

        model, dims = embedder.model, embedder.dimensions
        # One store per width. The column type refuses a wrong-width row.
        cached = (VectorCache() if vector_cache is None else vector_cache).at_width(dims)

        # Content-keyed, not id-keyed: curation rewrites a summary under the same id
        # (ADR 0005). Keyed on the indexed text, so the embedded string is the one
        # with lexical postings.
        keys: dict[str, str] = {}
        for entry in by_id.values():
            text = indexed_text[entry.id]
            keys.setdefault(text, cache_key(text, model=model, dimensions=dims))

        absent = set(cached.missing(list(keys.values())))
        missing = [text for text, key in keys.items() if key in absent]
        # Before the miss check. A build that reuses every row is what a repointed
        # gateway looks like when the corpus has not changed, and it must still
        # be checked. Memoised per store.
        _refuse_a_mixed_vector_space(cached, keys, absent, embedder=embedder)
        if missing:
            embedded = embedder.embed(missing)
            if len(embedded) != len(missing):
                raise ValueError("embedder returned the wrong number of vectors")
            cached.add({keys[text]: vec for text, vec in zip(missing, embedded, strict=True)})

        vectors = VectorStore(dims)
        vectors.load_from(cached, [(keys[indexed_text[e.id]], e.id) for e in by_id.values()])

    return UnifiedIndex(
        entries=by_id,
        lexical=lexical,
        vectors=vectors,
        embedder_model=model,
        embedder_dimensions=dims,
    )
