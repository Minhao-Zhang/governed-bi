"""One indexed string for both channels, and one embedding space in one store.

The cache key is model, dimensions, and text, so a gateway repointed behind the same id still hits.
"""

from __future__ import annotations

import pytest

from governed_bi.model.deterministic_embedder import DeterministicEmbedder
from governed_bi.registry.assets import AssetType
from governed_bi.retrieve import index as index_mod
from governed_bi.retrieve.index import IndexEntry, build_index
from governed_bi.retrieve.semantic import cache_key
from governed_bi.retrieve.vector_cache import VectorCache

WIDTH = 32


class _Counted:
    """An embedder with a fixed id, so two salts still share a cache key. A fingerprint in the id could never collide.
    """

    requested_model = "gateway/embed-v1"
    model = "gateway/embed-v1"
    dimensions = WIDTH

    def __init__(self, *, salt: str = "") -> None:
        self._real = DeterministicEmbedder(dimensions=WIDTH, salt=salt)
        self.texts: list[str] = []

    def embed(self, texts):
        self.texts.extend(texts)
        return self._real.embed(list(texts))


def _reopen(uri) -> VectorCache:
    """A second ``memory://`` connection sees no tables, so only a reopened directory models rows another process wrote.
    """
    return VectorCache(uri=uri)


def _entry(asset_id: str, summary: str) -> IndexEntry:
    return IndexEntry(
        id=asset_id, summary=summary, asset_type=AssetType.table, schema_tag="s"
    )


# ── I8: one text ─────────────────────────────────────────────────────────────


def test_the_embedded_text_is_the_indexed_text() -> None:
    """The cache key must hold the string BM25 has postings for, not the raw field."""
    embedder = _Counted()
    cache = VectorCache()
    build_index([_entry("s.t", "  orders one row per order\n")], embedder=embedder,
                vector_cache=cache)

    canary = cache_key(index_mod._SPACE_CANARY_TEXT, model=embedder.model, dimensions=WIDTH)
    keys = [k for k in cache.at_width(WIDTH).keys() if k != canary]
    assert keys == [cache_key("orders one row per order", model=embedder.model,
                              dimensions=WIDTH)], (
        f"the vector was keyed on a different string than the lexical channel indexed: {keys}"
    )


def test_whitespace_only_curation_does_not_re_embed() -> None:
    """Two summaries that differ only in surrounding whitespace are one document and must be one embed."""
    embedder = _Counted()
    cache = VectorCache()
    build_index([_entry("s.t", "orders one row per order")], embedder=embedder,
                vector_cache=cache)
    # The canary is not a corpus embed, so it is excluded rather than counted.
    corpus = [t for t in embedder.texts if t != index_mod._SPACE_CANARY_TEXT]
    assert corpus == ["orders one row per order"], "precondition: the first build embeds"

    build_index([_entry("s.t", "\n  orders one row per order  ")], embedder=embedder,
                vector_cache=cache)
    corpus = [t for t in embedder.texts if t != index_mod._SPACE_CANARY_TEXT]
    assert len(corpus) == 1, f"reflowing a summary re-embedded it: {corpus[1:]}"


# ── I9: one space ────────────────────────────────────────────────────────────


def test_a_store_written_by_another_model_behind_the_same_id_is_refused(tmp_path) -> None:
    """Hits from the old space and misses from the new one. A repoint inside one process, after the check has run, is
    not seen again."""
    uri = tmp_path / "store"
    build_index([_entry("s.a", "alpha one")], embedder=_Counted(),
                vector_cache=VectorCache(uri=uri))

    cache = _reopen(uri)
    repointed = _Counted(salt="the-gateway-moved")
    with pytest.raises(ValueError, match="different embedding space"):
        build_index(
            # `s.a` hits the cached row written by the first embedder; `s.b` is a miss.
            [_entry("s.a", "alpha one"), _entry("s.b", "beta two")],
            embedder=repointed,
            vector_cache=cache,
        )


def test_a_repointed_gateway_is_caught_with_no_cache_miss_at_all(tmp_path) -> None:
    """An unchanged corpus is all hits, so a check that runs only on misses never sees a repoint."""
    uri = tmp_path / "store"
    entries = [_entry("s.a", "alpha one"), _entry("s.b", "beta two")]
    build_index(entries, embedder=_Counted(), vector_cache=VectorCache(uri=uri))

    cache = _reopen(uri)
    with pytest.raises(ValueError, match="different embedding space"):
        build_index(entries, embedder=_Counted(salt="the-gateway-moved"), vector_cache=cache)


def test_a_late_row_from_another_space_is_probed(tmp_path) -> None:
    """Probes must include the end of the store. A partial re-embed of late keys is invisible to a sample of the first
    two thirds."""
    good, bad = _Counted(), _Counted(salt="the-gateway-moved")
    uri = tmp_path / "store"
    store = VectorCache(uri=uri).at_width(WIDTH)
    texts = ["aa alpha", "bb beta", "cc gamma", "zz omega"]
    # Three rows from one space and the last, alphabetically, from another.
    store.add(
        {
            cache_key(t, model=good.model, dimensions=WIDTH): v
            for t, v in zip(texts[:3], good.embed(texts[:3]))
        }
    )
    store.add({cache_key(texts[3], model=bad.model, dimensions=WIDTH): bad.embed([texts[3]])[0]})
    assert len(store) == 4, "precondition: four rows, no canary, so the bootstrap path runs"

    with pytest.raises(ValueError, match="different embedding space"):
        build_index(
            [_entry(f"s.{i}", t) for i, t in enumerate(texts)],
            embedder=good,
            # Reopened, so `opened_with` reports the four rows as pre-existing.
            vector_cache=_reopen(uri),
        )


def test_minting_examines_the_rows_it_vouches_for_even_when_the_build_reuses_none(
    tmp_path,
) -> None:
    """Minting must examine rows the build does not reuse. An empty reused set would vouch for a store it never looked
    at."""
    uri = tmp_path / "store"
    build_index(
        [_entry("s.a", "alpha one"), _entry("s.b", "beta two")],
        embedder=_Counted(),
        vector_cache=VectorCache(uri=uri),
    )
    # A cold build mints nothing, so this store already is what the bootstrap path is for: rows
    # with no canary vouching for them.
    canary = cache_key(index_mod._SPACE_CANARY_TEXT, model=_Counted().model, dimensions=WIDTH)
    assert canary not in VectorCache(uri=uri).at_width(WIDTH).keys(), (
        "precondition: no canary on record, so minting is what the reopen below does"
    )

    with pytest.raises(ValueError, match="different embedding space"):
        build_index(
            # Nothing in common with what is stored, so `reused` is empty.
            [_entry("s.x", "gamma three"), _entry("s.y", "delta four")],
            embedder=_Counted(salt="the-gateway-moved"),
            vector_cache=_reopen(uri),
        )


def test_a_late_row_is_probed_even_when_this_build_reuses_an_early_one(tmp_path) -> None:
    """The first reused row is the alphabetical start. A contaminated row at the end still has to be probed."""
    good, bad = _Counted(), _Counted(salt="the-gateway-moved")
    uri = tmp_path / "store"
    store = VectorCache(uri=uri).at_width(WIDTH)
    texts = ["aa alpha", "mm middle", "zz omega"]
    store.add({
        cache_key(t, model=good.model, dimensions=WIDTH): v
        for t, v in zip(texts[:2], good.embed(texts[:2]))
    })
    store.add({cache_key(texts[2], model=bad.model, dimensions=WIDTH): bad.embed([texts[2]])[0]})

    with pytest.raises(ValueError, match="different embedding space"):
        build_index(
            [_entry("s.a", texts[0])],   # reuses only the early, good row
            embedder=good,
            vector_cache=_reopen(uri),
        )


def test_the_check_is_paid_once_per_process_not_once_per_build() -> None:
    """One canary per process, not per build. The memo is per store and embedder, so it cannot hide a different one."""
    embedder = _Counted()
    cache = VectorCache()
    entries = [_entry("s.a", "alpha one")]
    build_index(entries, embedder=embedder, vector_cache=cache)
    after_first = len(embedder.texts)

    for _ in range(5):
        build_index(entries, embedder=embedder, vector_cache=cache)
    assert len(embedder.texts) == after_first, (
        f"five fully cached rebuilds made {len(embedder.texts) - after_first} extra call(s)"
    )


def test_a_cold_store_costs_nothing_when_the_build_reuses_nothing() -> None:
    """Nothing reused means nothing to probe. A cold store may not embed or store anything extra."""
    embedder = _Counted()
    cache = VectorCache()
    build_index([_entry("s.a", "alpha one")], embedder=embedder, vector_cache=cache)

    assert embedder.texts == ["alpha one"], (
        f"a cold build made a call it did not need: {embedder.texts}"
    )
    assert not cache.at_width(WIDTH).space_is_verified((embedder.model, WIDTH)), (
        "a cold store recorded a result it never reached"
    )


def test_the_same_embedder_adding_rows_is_not_refused() -> None:
    """Growing the corpus must not raise. The cold path probes one reused row and embeds the miss."""
    embedder = _Counted()
    cache = VectorCache()
    build_index([_entry("s.a", "alpha one")], embedder=embedder, vector_cache=cache)
    before = len(embedder.texts)

    index = build_index(
        [_entry("s.a", "alpha one"), _entry("s.b", "beta two")],
        embedder=embedder,
        vector_cache=cache,
    )
    assert index.vectors is not None and len(index.vectors) == 2
    assert embedder.texts[before:] == ["alpha one", "beta two"], (
        f"one probe of a reused row plus the miss, and nothing else: {embedder.texts[before:]}"
    )


def test_a_repoint_within_one_process_is_caught_when_anything_misses() -> None:
    """A same-id repoint is not a different cache key. A cold store that both reuses and misses must still refuse."""
    cache = VectorCache()
    build_index([_entry("s.a", "alpha one")], embedder=_Counted(), vector_cache=cache)

    with pytest.raises(ValueError, match="different embedding space"):
        build_index(
            [_entry("s.a", "alpha one"), _entry("s.b", "beta two")],
            embedder=_Counted(salt="the-gateway-moved"),
            vector_cache=cache,
        )


def test_a_repoint_with_nothing_to_embed_is_a_stated_limit() -> None:
    """A full hit makes no embed call, so there is no new vector to compare. The next process catches it on the canary.
    """
    cache = VectorCache()
    entries = [_entry("s.a", "alpha one")]
    build_index(entries, embedder=_Counted(), vector_cache=cache)

    # No raise, and that is the documented behaviour rather than an oversight.
    build_index(entries, embedder=_Counted(salt="the-gateway-moved"), vector_cache=cache)


def test_a_cold_cache_is_not_probed() -> None:
    """The paired negative of the refusal tests: a build must not re-embed its own rows."""
    embedder = _Counted()
    build_index(
        [_entry("s.a", "alpha one"), _entry("s.b", "beta two")],
        embedder=embedder,
        vector_cache=VectorCache(),
    )
    assert sorted(embedder.texts) == ["alpha one", "beta two"], (
        f"a build with no cache hits paid for a probe: {embedder.texts}"
    )


def test_a_fully_warm_cache_in_a_fresh_process_is_still_checked(tmp_path) -> None:
    """A warm reopen is still checked. No misses is what a repointed gateway with an unchanged corpus looks like."""
    embedder = _Counted()
    uri = tmp_path / "store"
    entries = [_entry("s.a", "alpha one"), _entry("s.b", "beta two")]
    build_index(entries, embedder=embedder, vector_cache=VectorCache(uri=uri))

    before = len(embedder.texts)
    build_index(entries, embedder=embedder, vector_cache=_reopen(uri))
    minting = embedder.texts[before:]
    assert minting[0] == index_mod._SPACE_CANARY_TEXT
    assert sorted(minting[1:]) == ["alpha one", "beta two"], (
        f"minting must verify the rows it vouches for: {minting}"
    )

    before = len(embedder.texts)
    build_index(entries, embedder=embedder, vector_cache=_reopen(uri))
    assert embedder.texts[before:] == [index_mod._SPACE_CANARY_TEXT], (
        "with a canary on record a warm reopen must cost exactly the canary: "
        f"{embedder.texts[before:]}"
    )
