"""The store's cosine must match the reference, and its silences must not look like a measurement.

A stored zero and a zero query are errors in the reference definition. Ties break by asset id. An empty candidate set is
a measurement; a missing embedder is not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


#: Wide enough that float32 rounding has somewhere to accumulate, narrow enough to stay fast.
WIDTH = 256

#: Unrelated topics. Near-synonyms would make the ordering assertions a coin toss, which is
#: the failure mode of a ranking test that looks strict and is not.
CORPUS: list[tuple[str, str]] = [
    ("sales.customers", "customers registered in the sales schema"),
    ("sales.orders", "one row per customer order with its total"),
    ("sales.invoices", "billing document issued against an order"),
    ("plant.capacity", "brewing capacity per factory site"),
    ("plant.shifts", "employee shift rota for the packaging line"),
    ("geo.sites", "geographic coordinates of each location"),
    ("supply.contracts", "supplier contracts and their renewal dates"),
    ("depot.returns", "returned bottles counted at the depot"),
    ("finance.ledger", "general ledger postings by accounting period"),
    ("hr.headcount", "headcount by department and month"),
]


def _entries(corpus: list[tuple[str, str]] | None = None) -> list:
    from governed_bi.registry.assets import AssetType
    from governed_bi.retrieve.index import IndexEntry

    return [
        IndexEntry(id=asset_id, summary=summary, asset_type=AssetType.table, schema_tag="s")
        for asset_id, summary in (CORPUS if corpus is None else corpus)
    ]


def _stored(store, key: str) -> list[float]:
    """Read back through Arrow. A getter that only tests call is a method nothing in the program needs."""
    arrow = store.to_arrow()
    return arrow.column("vector")[arrow.column("key").to_pylist().index(key)].as_py()


def _built(width: int = WIDTH, corpus: list[tuple[str, str]] | None = None):
    from governed_bi.model import DeterministicEmbedder
    from governed_bi.retrieve.index import build_index

    embedder = DeterministicEmbedder(dimensions=width)
    return build_index(_entries(corpus), embedder=embedder), embedder


# ── the arithmetic ────────────────────────────────────────────────────────────


def test_the_store_scores_and_orders_exactly_as_cosine() -> None:
    """Forgetting cosine distance yields squared L2, which still looks like a ranking. Float32 storage must not reorder.
    """
    from governed_bi.retrieve.semantic import cosine

    index, embedder = _built()
    store = index.vectors
    assert store is not None

    # Deliberately overlaps most of the corpus: a query that matched two rows would leave
    # eight tied at zero and the ordering claims below would be about the tiebreak alone.
    query = embedder.embed(
        ["customer order invoice factory site shift location supplier depot ledger headcount capacity"]
    )[0]
    ranked = store.search(query)
    assert len(ranked) == len(CORPUS), "the store dropped a row from an unfiltered search"

    # (1) same number, on the same vectors. The tolerance is float32 round-trip plus a
    # different summation order, both of which are storage facts; a metric change is orders
    # of magnitude away from it and would be caught here rather than absorbed.
    for asset_id, score in ranked:
        assert score == pytest.approx(cosine(query, _stored(store, asset_id)), abs=1e-6)
        assert -1.0 <= score <= 1.0

    # (2) same order.
    ours = sorted(
        ((asset_id, cosine(query, _stored(store, asset_id))) for asset_id, _ in ranked),
        key=lambda pair: (-pair[1], pair[0]),
    )
    theirs = sorted(ranked, key=lambda pair: (-pair[1], pair[0]))
    assert [a for a, _ in theirs] == [a for a, _ in ours]

    # (3) and the same order as the float64 vectors the embedder produced, so float32 storage
    # reordered nothing.
    summaries = [summary for _, summary in CORPUS]
    exact = dict(zip([a for a, _ in CORPUS], embedder.embed(summaries), strict=True))
    unrounded = sorted(
        ((asset_id, cosine(query, exact[asset_id])) for asset_id, _ in ranked),
        key=lambda pair: (-pair[1], pair[0]),
    )
    assert [a for a, _ in theirs] == [a for a, _ in unrounded]

    # Controls: the fixture must discriminate, or every assertion above is satisfied by a
    # store that returns one constant. Seven distinct values over ten rows -- with three
    # genuine ties, which is what makes the id tiebreak here too.
    assert len({round(score, 6) for _, score in ranked}) >= 7
    assert theirs[0][1] > theirs[1][1]


def test_a_candidate_restriction_returns_the_same_scores_as_no_restriction() -> None:
    """The prefilter must not be a second definition of the score. A candidate set that covers the table takes the scan
    path."""
    index, embedder = _built()
    store = index.vectors
    assert store is not None
    query = embedder.embed(["ledger postings"])[0]

    everything = dict(store.search(query))
    few = {"finance.ledger", "hr.headcount", "geo.sites"}
    assert dict(store.search(query, keys=few)) == {k: everything[k] for k in few}
    # The other branch: a candidate set that covers the table takes the scan path.
    assert dict(store.search(query, keys=set(everything))) == everything


# ── the silences ──────────────────────────────────────────────────────────────


def test_a_zero_vector_is_refused_at_write_rather_than_dropped_at_read() -> None:
    """A stored zero disappears from a cosine result with no error. Refuse it at write."""
    from governed_bi.retrieve.vectors import VectorStore

    store = VectorStore(4)
    with pytest.raises(ValueError, match="all-zero"):
        store.add({"a": [1.0, 0.0, 0.0, 0.0], "z": [0.0, 0.0, 0.0, 0.0]})


def test_a_zero_query_vector_raises_rather_than_returning_no_matches() -> None:
    """The other direction, and the worse one: LanceDB returns ``[]`` for a zero query,
    which is indistinguishable from "no candidate matched". ``semantic_search`` would then
    report ``ChannelState.ran`` over an empty ranking — a channel that measured nothing,
    claiming it measured."""
    from governed_bi.retrieve.vectors import VectorStore

    store = VectorStore(4)
    store.add({"a": [1.0, 0.0, 0.0, 0.0]})
    with pytest.raises(ValueError, match="zero vector"):
        store.search([0.0, 0.0, 0.0, 0.0])


def test_a_query_of_the_wrong_width_raises_the_way_cosine_does() -> None:
    """v1's incident, in its new clothes. LanceDB *does* raise here — but only when the
    vector column is named on the query; without that the message is *"There is no vector
    column in the data"*, which says nothing about width. The refusal is made in Python so
    it does not depend on remembering to name the column."""
    from governed_bi.retrieve.vectors import VectorStore

    store = VectorStore(4)
    store.add({"a": [1.0, 0.0, 0.0, 0.0]})
    with pytest.raises(ValueError, match="width mismatch"):
        store.search([1.0, 0.0])


# ── the ranking invariants the caller owns ────────────────────────────────────


def test_ties_break_by_asset_id_and_not_by_insertion_order() -> None:
    """LanceDB's tie order is **insertion** order — stable across repeated queries and across
    a reopen, and different for two indexes built from the same assets in a different order.
    The contract is that two runs over one index cannot disagree, so the sort is redone in
    Python. Built in descending id order, so a store that leaked its own order would fail."""
    from governed_bi.registry.channels import ChannelState
    from governed_bi.retrieve.semantic import semantic_search

    shared = "identical summary shared by every one of these assets"
    tied = [(f"s.t{i}", shared) for i in (4, 3, 2, 1, 0)]
    index, embedder = _built(corpus=tied)
    ranked, state = semantic_search(index, embedder.embed(["anything at all"])[0])

    assert state is ChannelState.ran
    assert len({score for _, score in ranked}) == 1, "the fixture is not actually tied"
    assert [asset_id for asset_id, _ in ranked] == [f"s.t{i}" for i in range(5)]


def test_an_empty_candidate_set_ran_and_an_unconfigured_channel_did_not() -> None:
    """An empty candidate set is a measurement. A missing embedder is the absence of an instrument. Both arrive as
    ``[]``."""
    from governed_bi.registry.channels import ChannelState
    from governed_bi.retrieve.index import build_index
    from governed_bi.retrieve.semantic import semantic_search

    index, embedder = _built()
    query = embedder.embed(["ledger postings"])[0]

    assert semantic_search(index, query, candidates=set()) == ([], ChannelState.ran)
    assert semantic_search(index, query, candidates={"nothing.here"}) == ([], ChannelState.ran)

    # No query vector, and no embedder: two ways to have no instrument, both `not_configured`.
    assert semantic_search(index, None) == ([], ChannelState.not_configured)
    bare = build_index(_entries())
    assert bare.vectors is None, "an index built without an embedder opened a store anyway"
    assert semantic_search(bare, query) == ([], ChannelState.not_configured)


# ── how much the store allocates, as a mechanism rather than as a benchmark ───


def test_keys_does_not_read_the_vector_column() -> None:
    """``keys`` projects the key column. A full-table read stays green if the assertion watches a different scan."""
    from governed_bi.retrieve.vectors import VectorStore

    store = VectorStore(4)
    store.add({"a": [1.0, 0.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0, 0.0]})

    # Asserted on the projection ``keys()`` asks for. Patching ``to_arrow`` or a separate scan
    # stays green when ``keys()`` drops ``.select``, so both the spy and the refusing patch are here.
    class _Spy:
        def __init__(self, real):
            self._real = real
            self.selected: list[str] | None = None

        def select(self, columns):
            self.selected = list(columns)
            return self._real.select(columns)

        def __getattr__(self, name):
            return getattr(self._real, name)

    spies: list[_Spy] = []
    real_search = store._table.search

    def _spying_search(*args, **kwargs):
        spy = _Spy(real_search(*args, **kwargs))
        spies.append(spy)
        return spy

    # **Both halves are needed and each was shipped alone once.** The spy alone is green against a
    # `keys()` that calls `to_arrow()` and *then* projects; the raising patch alone is green against
    # one that drops the projection from a `search()` it still makes. Third version.
    real_to_arrow = store._table.to_arrow

    def _refuse(*args, **kwargs):
        raise AssertionError("keys() read the whole table, vectors included")

    store._table.search = _spying_search  # type: ignore[method-assign]
    store._table.to_arrow = _refuse  # type: ignore[method-assign]
    try:
        keys = store.keys()
    finally:
        store._table.search = real_search  # type: ignore[method-assign]
        store._table.to_arrow = real_to_arrow  # type: ignore[method-assign]

    assert sorted(keys) == ["a", "b"]
    assert len(spies) == 1, f"keys() made {len(spies)} scans"
    assert spies[0].selected == ["key"], (
        f"keys() asked for {spies[0].selected}; without a projection the scan reads every vector"
    )
    assert keys == real_to_arrow().column("key").to_pylist(), (
        "the projected scan must return exactly the keys, and the order, that a full read would"
    )


def test_replace_reconnects_rather_than_reusing_the_connection() -> None:
    """Overwriting on a retained connection leaks committed pages. Reconnect before the overwrite, on every call."""
    from governed_bi.retrieve.vectors import VectorStore

    source = VectorStore(4)
    source.add({"a": [1.0, 0.0, 0.0, 0.0]})
    target = VectorStore(4)
    before = target._db

    target.load_from(source, [("a", "t1")])
    assert target._db is not before, "the table was overwritten on the retained connection"
    assert target.keys() == ["t1"], "the reconnect must not lose the rows it just wrote"

    # Reconnect before the overwrite. A reconnect after ``create_table`` still changes the
    # object and leaves the leak. Hold the connection objects: ``id()`` is recycled once the
    # old one is collected.
    seen = [target._db]
    for attempt in range(3):
        stale = target._db
        used_stale: list[str] = []
        real_create = stale.create_table

        def _mark(*args, **kwargs):
            used_stale.append("yes")
            return real_create(*args, **kwargs)

        stale.create_table = _mark  # type: ignore[method-assign]
        target.load_from(source, [("a", "t1")])
        stale.create_table = real_create  # type: ignore[method-assign]

        assert not used_stale, (
            f"call {attempt}: the overwrite ran on the connection it was meant to replace, so the "
            "reconnect happens after `create_table` and the leak is untouched"
        )
        assert all(target._db is not db for db in seen), (
            f"call {attempt}: the connection was reused"
        )
        seen.append(target._db)
        assert target.keys() == ["t1"]

    # A separate source, not the store as its own source: `load_from(store, ...)` only worked
    # because `to_arrow()` materialises before `_replace` destroys it, so it would break the moment
    # that read becomes lazy — which is the next fix recorded as audit P2.


def test_load_from_hands_the_writer_a_reader_and_never_a_whole_table() -> None:
    """The write is a reader, not a materialised table. Patch ``connect``, because ``_replace`` reconnects before it
    overwrites."""
    from governed_bi.retrieve.vectors import VectorStore

    source = VectorStore(4)
    source.add({f"k{i}": [float(i % 7), 0.0, 0.0, 1.0] for i in range(2500)})
    target = VectorStore(4)

    # Patched at `lancedb.connect`, not on `target._db`: `_replace` reconnects before it
    # overwrites (the P3 fix), so a patch on the store's current connection is never reached. Both
    # stores above already exist, so only the reconnect goes through this.
    handed: list[object] = []
    import governed_bi.retrieve.vectors as vectors_module

    real_connect = vectors_module.lancedb.connect

    class _Recording:
        def __init__(self, db):
            self._db = db

        def create_table(self, name, data, **kwargs):
            handed.append(data)
            return self._db.create_table(name, data, **kwargs)

        def __getattr__(self, attr):
            return getattr(self._db, attr)

    vectors_module.lancedb.connect = lambda uri: _Recording(real_connect(uri))

    batch_counts: list[int] = []
    real_batches = source._batches

    def _counting(**kwargs):
        for batch in real_batches(**kwargs):
            batch_counts.append(batch.num_rows)
            yield batch

    def _refuse(*args, **kwargs):
        raise AssertionError("load_from materialised the whole source table")

    real_to_arrow = source._table.to_arrow
    source._batches = _counting  # type: ignore[method-assign]
    source._table.to_arrow = _refuse  # type: ignore[method-assign]
    try:
        target.load_from(source, [(f"k{i}", f"asset.{i}") for i in range(2500)])
    finally:
        source._table.to_arrow = real_to_arrow  # type: ignore[method-assign]
        vectors_module.lancedb.connect = real_connect

    assert len(batch_counts) > 1, (
        f"the source was consumed in {len(batch_counts)} batch(es), so it was not streamed"
    )
    assert handed and isinstance(handed[0], pa.RecordBatchReader), (
        f"create_table was handed {type(handed[0]).__name__}, not a RecordBatchReader — a "
        "materialised table is what the +626 MB of net saving came from removing"
    )
    assert len(target) == len(target.keys()) == 2500
    assert sorted(target.keys()) == sorted(f"asset.{i}" for i in range(2500))


def test_every_asset_gets_its_own_vector() -> None:
    """Reversing the pairing inside a batch still returned rows. Each vector here is one-hot so a swap is obvious."""
    from governed_bi.retrieve.vectors import VectorStore

    width = 8
    source = VectorStore(width)
    onehot = {}
    for i in range(width):
        vector = [0.0] * width
        vector[i] = 1.0
        onehot[f"cache.{i}"] = vector
    source.add(onehot)

    target = VectorStore(width)
    target.load_from(source, [(f"cache.{i}", f"asset.{i}") for i in range(width)])

    for i in range(width):
        probe = [0.0] * width
        probe[i] = 1.0
        best = max(target.search(probe), key=lambda pair: pair[1])
        assert best[0] == f"asset.{i}", (
            f"asset.{i} carries another asset's vector: the nearest row to its own probe is {best}"
        )
        assert best[1] == pytest.approx(1.0)


def test_two_assets_sharing_one_summary_both_get_the_vector() -> None:
    """One cache entry, two asset ids — the reason ``pairs`` is a sequence and not a mapping.

    The streaming rewrite has to fan a source row out to every key that wants it *within a batch*,
    which the old full-permutation ``take()`` did for free.
    """
    from governed_bi.retrieve.semantic import cosine
    from governed_bi.retrieve.vectors import VectorStore

    source = VectorStore(4)
    source.add({"shared": [0.0, 1.0, 0.0, 0.0]})
    target = VectorStore(4)
    target.load_from(source, [("shared", "a.one"), ("shared", "b.two")])

    assert sorted(target.keys()) == ["a.one", "b.two"]
    scored = dict(target.search([0.0, 1.0, 0.0, 0.0]))
    assert scored["a.one"] == pytest.approx(1.0)
    assert scored["b.two"] == pytest.approx(1.0)
    assert cosine([0.0, 1.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_a_missing_source_key_raises_before_anything_is_written() -> None:
    """Membership is checked before any batch. A streaming writer cannot discover a missing key once the table write has
    started."""
    from governed_bi.retrieve.vectors import VectorStore

    source = VectorStore(4)
    source.add({"present": [1.0, 0.0, 0.0, 0.0]})
    target = VectorStore(4)
    target.add({"old": [0.0, 0.0, 0.0, 1.0]})

    with pytest.raises(KeyError, match="absent"):
        target.load_from(source, [("present", "a.one"), ("absent", "a.two")])

    assert target.keys() == ["old"], (
        f"the target was modified before the refusal: {target.keys()}"
    )


def test_the_row_count_comes_from_the_table_and_not_from_the_caller() -> None:
    """The caller's count can disagree with a reader. ``search`` limits by the stored count, so an inflated count
    returns a subset."""
    from governed_bi.retrieve.vectors import VectorStore, _schema

    store = VectorStore(4)
    with pytest.raises(ValueError, match="0 were written"):
        store._replace(pa.RecordBatchReader.from_batches(_schema(4), iter(())), 5)

    # And the ordinary path stays consistent: the two ways of asking agree.
    store.add({"a": [1.0, 0.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0, 0.0]})
    target = VectorStore(4)
    target.load_from(store, [("a", "one"), ("b", "two")])
    assert len(target) == target._table.count_rows() == len(target.keys()) == 2
