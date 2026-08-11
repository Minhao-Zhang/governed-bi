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
    # Imported for real inside ``build_index``, and only when an embedder is passed:
    # ``import lancedb`` costs ~1.1 s that every no-embedder caller (most test builds, and
    # every ``langgraph dev`` reload) would otherwise pay for a store it never opens.
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

    The declared table in function form — the one answer to which schema an asset votes for.
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


#: How alike two embeds of one text must be to be the same vector space.
#:
#: Deliberately **not** a register knob. A knob is a comparability dial — a value an arm might
#: legitimately want set differently, recorded so two runs can be told apart. This one only ever
#: decides whether a corrupt store raises, so an arm choosing its own value is an arm choosing
#: not to be checked. The margin is wide in both directions: one model embedding one text twice
#: agrees to better than 0.9999, and two different models occupy unrelated subspaces of the same
#: width, so they agree at roughly the cosine of two arbitrary vectors.
_SAME_SPACE_COSINE = 0.99

#: How many cached corpus rows to probe when the canary is being written for the first time.
#:
#: More than one because a *partly* re-embedded store holds rows from both spaces and a single probe
#: landing on a new row reports "fine". **Three is not a defence against a small partial re-embed
#: and the comment here used to imply it was**: at ``1 - (1 - p)**3``, a 1% contamination of the
#: 14,613-row store is caught 3% of the time. What three probes reliably catch is a *whole*-store
#: move, which is the shape a repointed gateway actually produces, and the canary catches that on
#: its own. The ends and the middle rather than three from one neighbourhood.
_SPACE_PROBES = 3

#: The canary's text. Short, so the call costs a handful of tokens, and fixed, so its row is the
#: same row in every store this repository writes. Content-keyed like every other row, which is
#: what lets it live in the same table without a schema change.
_SPACE_CANARY_TEXT = "governed-bi vector space canary"

def _refuse_a_mixed_vector_space(
    cached: VectorStore,
    keys: Mapping[str, str],
    absent: set[str],
    *,
    embedder: Embedder,
) -> None:
    """Refuse to build from a store whose existing rows came from a different embedder.

    **The cache key cannot catch this** (audit I9). It is ``model|dimensions|text``, so a gateway
    repointed at a different model behind the same id — the normal shape of a self-hosted LiteLLM
    or Bedrock proxy — produces cache *hits* on every old row while anything new is embedded by
    the new model. Width matches, ``corpus_content_hash`` does not move, ``embedder_model`` reads
    the same string, and one index then holds two vector spaces in which cosine between them is
    noise. That is the v1 incident this file's sealed contract opens with, in the one form the key
    it prescribes cannot see.

    **The check is a canary row, and the first version of it was in the wrong place.** It ran only
    when there were misses to write, on the reasoning that a build reusing every row is a build
    that mixes nothing. That is exactly backwards: repoint the gateway and leave the corpus alone —
    the *most likely* shape of this incident — and there are no misses at all, so the check never
    ran, the index was built entirely from old-space vectors, and serve then queried it with
    new-space question vectors. The docstring sold that case as a cost saving and a test asserted
    the probe stayed unmade. Caught in review.

    So: one small embed per process, memoised per store, on every build whose store already held
    rows when this process opened it. The canary text is fixed, so its stored vector is a direct
    statement of which space wrote this store, and the bootstrap probes are sampled from the
    **store's own rows under this embedder's prefix** — not from what this build happens to reuse.

    ``opened_with > 0`` selects the canary path. It is **not** a proof that a store this process
    created is safe, and a previous version of this docstring said it was, on the reasoning that
    another embedder's rows sit under another key. That reasoning is circular: the incident is a
    gateway repointed behind an *unchanged* id. The cold path therefore probes too, and what
    bounds it is the sealed ``tests/model/test_embedder_contract.py``, which asserts a warm rebuild
    by the same embedder makes **zero** embedding calls. That forbids a probe on the one remaining
    case, not information theory — see the limit below.

    The corpus-row probes cover the case the canary cannot: a store written **before** the canary
    existed, where minting one would otherwise legitimise whatever model is current. Those rows are
    checked at the moment the canary is minted, sampled at the ends and the middle.

    **Stated limit, and it is a contract, not a law.** One case is unchecked: a repoint inside a
    single process, against a store that same process created, where the later build hits every row.
    A probe target *does* exist — ``keys()`` carries every row's plaintext — so this is not
    "information-theoretically undetectable", which an earlier version of this claimed. It is
    forbidden by ``tests/model/test_embedder_contract.py``'s assertion that a warm rebuild by the
    same embedder embeds nothing, and that contract is sealed. Bounded: the next process to open the
    store compares its canary and refuses. Separately, the memo means a long-lived server that never
    rebuilds its index will not notice a mid-life repoint.

    An embed failure here is **not** swallowed: this function's whole job is to be the check, and
    a check that passes when it could not run is worse than no check.
    """
    reused = sorted(text for text, key in keys.items() if key not in absent)
    if cached.opened_with == 0:
        # **A store this process created still needs one check, and the reasoning that said
        # otherwise was circular.** It read: rows written by another embedder in this process are
        # under a different key, since the key carries the model. True — and irrelevant, because
        # the incident is a gateway repointed *behind the same id*, which is why `_Counted` in the
        # test file reports a fixed one. So: cold store, build with A, repoint, build again with A'
        # where some rows hit and some miss. Demonstrated in review: two spaces, no refusal.
        #
        # What is available here is a probe of a reused row, and only when this build also has
        # misses — because a build with none makes no call at all, so there is no new-space vector
        # to compare against and the mix is undetectable at zero cost. That is a real limit and it
        # is stated in the docstring rather than papered over.
        #
        # No canary is minted on this path. `tests/model/test_embedder_contract.py` is sealed and
        # asserts both `embedded == [summary]` and `len(cache) == 2`, so a cold store may neither
        # embed nor store anything extra. Its builds never hit this branch: they either miss
        # everything (different model, so different keys) or hit everything (no misses).
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
    # `VectorStore.space_is_verified` and not a module-level set: `MEMORY_URI` gives every ephemeral
    # store the same uri, so a process-wide key would let one in-memory store answer for another.
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
        # Bootstrap: this store predates the canary, so its rows have never been checked against
        # anything. **Sampled from the store, not from this build's reused set**, which is the
        # second thing review had to correct here. Keyed on `reused`, two holes were open: a build
        # that reuses nothing — a corpus rewrite, which this repository has just done — minted the
        # canary in the *new* space having examined none of the pre-existing rows and then stamped
        # the store verified forever; and `reused[0]` alone is the alphabetically-first row, which
        # is exactly the blind spot `_SPACE_PROBES` exists to close.
        #
        # A target always exists: `cache_key` is `model|dimensions|text`, so `keys()` — already
        # read once per build — carries the plaintext of every row. Only rows under **this
        # embedder's own prefix** are eligible, because a row under another model's prefix is not
        # a space this build can mix with, and because probing one would break the sealed
        # `tests/model/test_embedder_contract.py`, whose second build sees only the first's rows.
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
    """``fresh`` must agree with the row already under ``key``, or this is two spaces."""
    similarity = dict(cached.search(list(fresh), keys=[key])).get(key)
    if similarity is None:
        # It was reported present a moment ago. A store that loses a row between `missing` and
        # `search` is not a store this build can reason about.
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
    """Build the unified index from summary-bearing entries.

    Blank summaries refused (I1). Lexical always built. With ``embedder``, look up
    ``vector_cache`` by :func:`~.semantic.cache_key` (model + dimensions + text),
    embed misses once, write back. Cache without embedder raises. Vectors copied
    from the store in Arrow; ephemeral store if no cache supplied.
    """
    by_id: dict[str, IndexEntry] = {}
    docs: list[tuple[str, str]] = []
    #: id → the one text that enters **both** channels. The lexical side indexed
    #: ``summary.strip()`` while the cache key was built from the unstripped ``summary``, so the
    #: two channels scored different strings and a curation pass that changed only surrounding
    #: whitespace re-embedded an asset whose BM25 postings were byte-identical (audit I8). One
    #: variable, read by both, is the only shape in which they cannot drift again.
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

    # `k` from the register, not `BM25`'s own default. The two agreed at 1.2, which is why
    # nobody noticed `lexical_saturation_k` shipping UNSET while this line ran a literal.
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
        # One store per width. A wrong-width row cannot reach it: the column type is
        # `fixed_size_list[dims]`, so storage refuses what a dict cache had to check by hand.
        cached = (VectorCache() if vector_cache is None else vector_cache).at_width(dims)

        # One cache key per distinct summary. Content-keyed, not id-keyed, because curation
        # rewrites summaries in place under the same id (ADR 0005 §2.2). Keyed on the **indexed**
        # text, so the string BM25 has postings for is the string that was embedded (audit I8).
        keys: dict[str, str] = {}
        for entry in by_id.values():
            text = indexed_text[entry.id]
            keys.setdefault(text, cache_key(text, model=model, dimensions=dims))

        absent = set(cached.missing(list(keys.values())))
        missing = [text for text, key in keys.items() if key in absent]
        # **Outside `if missing:`, which is where it belongs.** A build that reuses *every* row is
        # the most dangerous one, not the safest: it is what a repointed gateway looks like when the
        # corpus has not changed. Memoised per store, so this is one small embed call, and only on a
        # store that already held rows when this process opened it, plus one probe on a cold store
        # whose build both reuses and misses. What bounds the case that stays open is the sealed
        # `tests/model/test_embedder_contract.py`, not an absence of anything to check.
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
