"""One text into both channels (audit I8), and one vector space in one store (audit I9).

Both are silent by construction, which is why they need tests rather than review:

* The lexical channel indexed ``summary.strip()`` while the vector cache key was built from the
  unstripped ``summary``. Nothing raises, both channels return scores, and the only symptom is
  that the two of them scored different strings.
* The cache key is ``model|dimensions|text``. A gateway repointed at a different model behind the
  same id therefore *hits* on every row written before the move and embeds every row after it.
  Width matches, ``corpus_content_hash`` does not move, and cosine between the two halves is
  noise — the v1 incident that ``tests/retrieve/test_scoring_contract.py`` opens with, in the one
  form the key it prescribes cannot see.
"""

from __future__ import annotations

import pytest

from governed_bi.model.deterministic_embedder import DeterministicEmbedder
from governed_bi.register.assets import AssetType
from governed_bi.retrieve import index as index_mod
from governed_bi.retrieve.index import IndexEntry, build_index
from governed_bi.retrieve.semantic import cache_key
from governed_bi.retrieve.vector_cache import VectorCache

WIDTH = 32


class _Counted:
    """An embedder reporting a **fixed** id, whatever it is actually computing.

    ``DeterministicEmbedder``'s own ``model`` carries a fingerprint of its salt, so two salts
    produce two cache keys and can never collide. That is the correct behaviour and it is also
    why it cannot model this defect: a repointed proxy keeps the id and changes the vectors.
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
    """A **new** cache over rows already on disk, which is what a second process sees.

    File-backed and not ``memory://``, and that is load-bearing rather than incidental: the check is
    gated on ``VectorStore.opened_with``, i.e. rows that were there before this process. A second
    ``lancedb.connect("memory://")`` sees no tables at all, so an in-memory store can never model
    "someone else wrote these rows" — clearing a memo would fake the symptom while skipping the
    condition. Reopening a directory is the real shape.
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
    """The consequence, as a cost rather than as a string.

    Two summaries that differ only in surrounding whitespace are one BM25 document and must be
    one embed. Keyed on the raw field they were two, so a curation pass that reflowed a summary
    paid to re-embed an asset whose postings were byte-identical.
    """
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
    """The mixing case: hits from the old space, misses embedded into the new one.

    ``_reopen`` is load-bearing and says something about the check's reach: the memo is keyed on
    ``(model, dimensions)``, and a repointed gateway reports the **same** model id — that is the
    whole incident — so within one process the check is paid once and a repoint after that is not
    seen. A gateway moves between processes, so this is a stated limit rather than a hole; what it
    does mean is that a long-lived server which never rebuilds its index will not notice a
    mid-life repoint, and no cheap check would, since query vectors are never cached.
    """
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
    """**The case the first version of this check could not see**, and the likeliest one.

    Repoint the gateway and leave the corpus alone: every row hits, so a check that ran only when
    there were misses to write never ran. The index was then built entirely from old-space
    document vectors and serve queried it with new-space question vectors — the v1 incident,
    intact, with width matching and ``corpus_content_hash`` unmoved.

    Same corpus, byte for byte. The only thing that changed is what is behind the model id.
    """
    uri = tmp_path / "store"
    entries = [_entry("s.a", "alpha one"), _entry("s.b", "beta two")]
    build_index(entries, embedder=_Counted(), vector_cache=VectorCache(uri=uri))

    cache = _reopen(uri)
    with pytest.raises(ValueError, match="different embedding space"):
        build_index(entries, embedder=_Counted(salt="the-gateway-moved"), vector_cache=cache)


def test_a_late_row_from_another_space_is_probed(tmp_path) -> None:
    """The bootstrap probes must cover the **end** of the store, not just its first two thirds.

    ``reused[::n // 3]`` samples 0, n/3 and 2n/3, so a partial re-embed confined to
    alphabetically-late assets — schemas s–z re-curated after the gateway moved — was invisible to
    the very check whose constant says three probes exist to prevent exactly that.

    Rows are written straight into the store rather than through ``build_index``, because that is
    what the case *is*: a store that predates the canary, holding rows from two spaces, which no
    sequence of well-behaved builds can produce.
    """
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
    """**The hole minting opened, found in the second review of this check.**

    A store written before the canary existed, reopened by a build whose texts all *miss* — a corpus
    rewrite, which this repository has just done — took the bootstrap path with an empty ``reused``
    set. So it minted the canary in the **new** embedder's space, examined none of the pre-existing
    rows, and called ``mark_space_verified``. Every later process then matched that canary and was
    satisfied forever, with two spaces in one index and cosine 0.5 between them.

    The probes are sampled from the store's own keys now, which is possible because ``cache_key`` is
    ``model|dimensions|text``: every row carries its plaintext, so a target always exists.
    """
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
    """``reused[0]`` was one probe at the alphabetically-first row — the named blind spot.

    Review built the case: the contaminated row is alphabetically last, and the build reuses an
    early row that the *new* embedder wrote, so a probe of ``reused[0]`` matches and passes. Probing
    the store's own keys at the ends and the middle is what closes it.
    """
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
    """A driver building a fresh index per question must not pay 1,351 canary calls.

    The memo is what makes an unconditional check affordable, so it is asserted rather than
    trusted — and it is keyed on ``(uri, model, dimensions)``, so it cannot mask a *different*
    store or a different embedder.
    """
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
    """A build that reuses no row has nothing to compare against, so it makes no extra call.

    **The name and docstring here claimed more than that** — "a store this process created is not
    checked at all", on the reasoning that another embedder's rows sit under another key. Circular,
    since the incident is a repoint behind an *unchanged* id, and the test below proves it. Caught in
    review.

    What survives is the narrow fact: nothing is reused, so nothing can be probed, and
    ``tests/model/test_embedder_contract.py`` — sealed — requires such a build to embed exactly the
    texts it needs.
    """
    embedder = _Counted()
    cache = VectorCache()
    build_index([_entry("s.a", "alpha one")], embedder=embedder, vector_cache=cache)

    assert embedder.texts == ["alpha one"], (
        f"a cold build made a call it did not need: {embedder.texts}"
    )
    assert not cache.at_width(WIDTH).space_is_verified((embedder.model, WIDTH)), (
        "a cold store recorded a verdict it never reached"
    )


def test_the_same_embedder_adding_rows_is_not_refused() -> None:
    """The paired negative. Growing a corpus is the normal case and must not raise.

    Without this, a check that raises on everything also passes the refusal tests.

    It costs one probe, and the docstring here used to claim otherwise — "the canary was minted by
    the first build and verified by this one from the memo" — which was true of no path at all: the
    cache is in-memory, so ``opened_with`` is 0, so there is no canary and the memo is empty. Caught
    in review. What actually runs is the cold-store probe of a reused row, which is the only thing
    that can catch a repoint behind an unchanged model id.
    """
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
    """**The hole the ``opened_with`` gate opened, found in review.**

    The gate's justification was that rows this process wrote came from an embedder it holds, and a
    row written by another embedder in the same process is under a different key because the key
    carries the model. The second half is true and irrelevant: the incident *is* a gateway repointed
    behind an unchanged id, which is why ``_Counted`` reports a fixed one.

    So this sequence produced two spaces in one index with nothing raised — cold store, build with
    A, repoint, build again where ``s.a`` hits and ``s.b`` misses. Measured then:
    ``cos(stored s.a, new-space "alpha one") = 0.0`` beside ``cos(stored s.b, new-space) = 1.0``.
    """
    cache = VectorCache()
    build_index([_entry("s.a", "alpha one")], embedder=_Counted(), vector_cache=cache)

    with pytest.raises(ValueError, match="different embedding space"):
        build_index(
            [_entry("s.a", "alpha one"), _entry("s.b", "beta two")],
            embedder=_Counted(salt="the-gateway-moved"),
            vector_cache=cache,
        )


def test_a_repoint_with_nothing_to_embed_is_a_stated_limit() -> None:
    """The other half, asserted so the limit is a fact in the suite rather than a docstring.

    A build that hits every row makes no embedding call, so there is no new-space vector to compare
    against and the mix is undetectable **at zero cost**. This is not a defect that can be fixed
    cheaply — it is information-theoretic. It is bounded, though: it needs the repoint to happen
    inside one process, after the store was created by that same process, and the very next process
    to open the store catches it on the canary.
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
    """**This test asserted the opposite and that was the defect.**

    It read ``test_a_fully_warm_cache_is_not_probed``: "no misses means nothing is being written, so
    there is nothing to invalidate". A repointed gateway with an unchanged corpus produces exactly
    no misses, so the assertion pinned the blind spot open.

    What a warm reopen costs is asserted in both of its forms, because they differ and both matter:
    the **first** one mints the canary and therefore pays to check the rows it is about to vouch
    for; every one after that pays the canary alone.
    """
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
