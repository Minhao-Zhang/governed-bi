"""What LanceDB does differently from a Python dict, and must not be allowed to.

The semantic channel scored an in-memory ``dict[str, list[float]]`` by
``retrieve/semantic.py::cosine`` until 2026-08-04. It now scores a LanceDB table, and the
storage change is invisible from every existing test: the old ones assert *rankings*, and a
store that quietly returned a different number for the same pair of vectors would still
produce a plausible ranking. So this file asserts the two things the swap could break and
nothing else was watching:

* **the arithmetic** — LanceDB's ``1 - _distance`` is our ``cosine``, on the same vectors,
  to float32 precision, and it orders them identically;
* **the silences** — a stored zero vector is *dropped* from a cosine result and a zero query
  vector returns ``[]``, both with no error. ``cosine`` raises on both, and
  ``model/deterministic_embedder.py`` keeps a zero-norm fallback *because* it raises.
  Turning either raise into an empty result would convert "this vector is degenerate" into
  "this asset was not a candidate", which is the absence-reads-as-zero shape this repository
  retires numbers over.

Plus the two ranking invariants that are properties of the *caller* and would survive any
amount of storage-layer correctness: ties break by asset id, and an empty candidate set is a
different observation from an unconfigured channel.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("E")

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
    from governed_bi.register.assets import AssetType
    from governed_bi.retrieve.index import IndexEntry

    return [
        IndexEntry(id=asset_id, summary=summary, asset_type=AssetType.table, schema_tag="s")
        for asset_id, summary in (CORPUS if corpus is None else corpus)
    ]


def _stored(store, key: str) -> list[float]:
    """The vector the store holds, read back through the same Arrow path ``load_from`` uses.

    Deliberately not a ``__getitem__`` on ``VectorStore``: nothing in ``src/`` needs to read
    one vector back, and a method that only tests call is the declared-but-unwired shape this
    repository treats as a defect.
    """
    arrow = store.to_arrow()
    return arrow.column("vector")[arrow.column("key").to_pylist().index(key)].as_py()


def _built(width: int = WIDTH, corpus: list[tuple[str, str]] | None = None):
    from governed_bi.model import DeterministicEmbedder
    from governed_bi.retrieve.index import build_index

    embedder = DeterministicEmbedder(dimensions=width)
    return build_index(_entries(corpus), embedder=embedder), embedder


# ── the arithmetic ────────────────────────────────────────────────────────────


def test_the_store_scores_and_orders_exactly_as_cosine() -> None:
    """The oracle. If LanceDB's cosine ever stops being our cosine, this is what says so.

    Three separate claims, because they fail separately:

    1. every returned similarity equals ``cosine(query, stored vector)`` — the conversion
       ``1 - _distance`` is exact and the metric really is cosine. Forgetting
       ``distance_type("cosine")`` yields *squared L2*, which is a plausible-looking ranking
       and is not this;
    2. the store's order equals the order that same ``cosine`` produces — the property every
       downstream assertion actually depends on;
    3. the order is unchanged by float32 storage, i.e. it also matches the order computed
       from the embedder's own float64 output. Storage precision costs ~6.6e-08 on a 3072-dim
       cosine, and this is the check that says the cost is precision and not rank.
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
    # genuine ties, which is what makes the id tiebreak load-bearing here too.
    assert len({round(score, 6) for _, score in ranked}) >= 7
    assert theirs[0][1] > theirs[1][1]


def test_a_candidate_restriction_returns_the_same_scores_as_no_restriction() -> None:
    """The prefilter is an *optimisation* and must not be a second definition of the score.

    Two code paths reach a score: a ``key IN (...)`` prefilter for a candidate set smaller
    than the table, and a full scan filtered in Python when it is not (measured: at full
    coverage the ``IN`` costs 451 ms against 244 ms). A restriction that changed a number
    would be invisible — every caller looks at one path per call.
    """
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
    """Measured: three rows in a table, ``limit(100)``, ``distance_type("cosine")``, and the
    zero-vector row simply **is not in the result** — no error, no NaN. It comes back
    normally under ``l2``. ``cosine`` says *"cosine of a zero vector is undefined"*.

    So the refusal moves to write time, which is also earlier and louder: the store cannot
    contain a row that a search would then silently omit.
    """
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
    from governed_bi.register.facets import ChannelState
    from governed_bi.retrieve.semantic import semantic_search

    shared = "identical summary shared by every one of these assets"
    tied = [(f"s.t{i}", shared) for i in (4, 3, 2, 1, 0)]
    index, embedder = _built(corpus=tied)
    ranked, state = semantic_search(index, embedder.embed(["anything at all"])[0])

    assert state is ChannelState.ran
    assert len({score for _, score in ranked}) == 1, "the fixture is not actually tied"
    assert [asset_id for asset_id, _ in ranked] == [f"s.t{i}" for i in range(5)]


def test_an_empty_candidate_set_ran_and_an_unconfigured_channel_did_not() -> None:
    """Four states arrive from LanceDB as the same ``[]``: no candidates, a filter that
    matched nothing, an empty table, and a zero query. They are different facts, so each is
    decided in Python before the store is asked anything.

    ``ran`` over an empty ranking is a *measurement* — "nothing of these types is a
    candidate". ``not_configured`` is the absence of an instrument. Collapsing them is how
    "the channel found nothing" and "the channel was never wired up" became one observation,
    which is the shape half this repository's retired numbers have.
    """
    from governed_bi.register.facets import ChannelState
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
    """``keys()`` said "without reading a single vector" while calling ``to_arrow()``.

    Asserted by **removing** the full read and by inspecting the projection actually asked for, not
    by timing: a benchmark in a test suite is a flake, and the claim is not "this is fast", it is
    "this does not touch the vectors". Measured on the real 14,613-row store, the projected scan
    returns the same keys in 1.77 MB against 181.3 MB for the whole table, and a 2 ms sampler
    catches the full read as a +407 MB working-set transient. ``missing()`` calls this once per
    index build.
    """
    from governed_bi.retrieve.vectors import VectorStore

    store = VectorStore(4)
    store.add({"a": [1.0, 0.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0, 0.0]})

    # **Asserted on the projection `keys()` itself asks for.** Two earlier versions of this were
    # green against the defect: the first patched `store._table.to_arrow`, which `keys()` does not
    # call, and the second built its own projected scan and asserted on *that* — so dropping
    # `.select([...])` from `keys()` changed nothing either time. Both caught by
    # `tools/mutate.py`'s `p1-keys-scan-drops-the-projection`, which is why it is declared.
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
    """Overwriting a table on a retained LanceDB connection leaks committed pages.

    Measured over 200 iterations at a 12.3 MB payload: **43.9 MB of private commit per call**,
    linear and payload-independent, with working set growing 12.4 MB per call beside it — so about a
    quarter is commit-only, not all of it as first claimed.

    **What that is worth on the real path is almost nothing, and this docstring said otherwise.**
    ``build_index`` constructs a fresh ``VectorStore`` before every ``load_from``, so ``_replace``
    runs once per store and nothing retains one: 0.226 MB per build before, 0.201 MB after, in
    isolated processes. The claim here was "~50 GB across a 1,351-question run", which described a
    loop no caller performs — caught in review, and the missing step was ``grep load_from src/``.
    The test stays because the mechanism is real and cheap to keep true.

    Asserted on the connection object and on the *order*, not on a counter: a commit-charge
    assertion in a test suite is a flake, and reconnecting after the overwrite would satisfy the
    object check while leaving the leak entirely in place.
    """
    from governed_bi.retrieve.vectors import VectorStore

    source = VectorStore(4)
    source.add({"a": [1.0, 0.0, 0.0, 0.0]})
    target = VectorStore(4)
    before = target._db

    target.load_from(source, [("a", "t1")])
    assert target._db is not before, "the table was overwritten on the retained connection"
    assert target.keys() == ["t1"], "the reconnect must not lose the rows it just wrote"

    # **The order matters and the first version of this test could not see it.** Reconnecting
    # *after* `create_table` leaves the leak fully in place while still satisfying "the connection
    # object changed", so the assertion above is not sufficient on its own. Caught in review.
    # **Every call, and in the right order.** A substring search over `inspect.getsource` was the
    # first attempt and a comment mentioning `lancedb.connect` satisfied it; one `load_from` also
    # cannot see a reconnect that happens only on the first call — which is precisely the retained
    # store this line is kept for. So: call it repeatedly and require a new connection each time.
    #
    # Asserted on **which connection performs the overwrite**, because "the connection object
    # changed" is satisfied by reconnecting *after* `create_table`, which leaves the leak entirely
    # in place. So: mark the old connection's `create_table` and require that it is never the one
    # called. Repeated, because a reconnect that fires only on the first call is invisible to a
    # single `load_from` — and a retained store is the only scenario the line is kept for.
    # **The objects, not their ids.** `seen = {id(...)}` was the first version and it flaked once in
    # four full-suite runs: CPython recycles `id()` once the old connection is collected, so a
    # genuinely new object can report an id already in the set. Holding the objects both prevents the
    # recycling and makes the comparison exact.
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
    """Audit P2. The **write** path is what the saving came from, so that is what is asserted.

    Two earlier versions of this test were green against the defect. The first patched
    ``source._table.to_arrow`` — but ``_batches()`` goes through ``_table.search()``, a different
    attribute, so an implementation reading the whole table through the neighbouring API passed.
    The second added ``assert 2500 > 1024``, which compares two literals.

    What is asserted now is the shape that earned the measured win: ``create_table`` receives a
    ``RecordBatchReader``, the source is consumed through ``_batches()``, and more than one batch is
    yielded. Reversed: an implementation that collects ``list(source._batches())`` into a table
    fails, and so does one that materialises the source.

    **The memory numbers are measured, not asserted** — +944 → +318 MB net and +1,473 → +566 MB peak
    on the real 14,613-row store — and they live in ``load_from``'s docstring and the P2 row. A
    memory assertion in a test suite is a flake; what a test can pin is the mechanism.
    """
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
    """The rewrite's whole purpose is re-keying, and nothing in its own tests checked the pairing.

    Review demonstrated it: reversing ``take`` inside each yielded batch — so every asset receives
    another asset's vector — passed all three tests added with the rewrite. A pre-existing test
    caught it, which made it a coverage gap rather than an open hole, but the gap was exactly on the
    property the change is about.

    Each vector below is one-hot, so a mispairing is a cosine of 0.0 rather than a near miss.
    """
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
    """The check moved to the front, and it had to.

    It used to fall out of the ``row_of`` lookup, which a streaming writer cannot do: by the time
    a generator discovers a missing key, ``create_table`` has already consumed batches. So
    membership is checked against ``keys()`` up front — 1.77 MB on the real store — and the target
    must be left exactly as it was.
    """
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
    """A regression the streaming rewrite introduced, found in review.

    ``_replace`` took ``count`` as an argument, but with a ``RecordBatchReader`` what gets written is
    decided by a generator *after* that number was passed. A reader yielding nothing left
    ``len(store) == 5`` against a table holding 0 rows — and ``search`` uses ``limit = self._rows``,
    so a store with an inflated count silently returns a subset of the rows it does have. ``add``
    already counted from the table, so ``count`` was a second writer of one field.
    """
    from governed_bi.retrieve.vectors import VectorStore, _schema

    store = VectorStore(4)
    with pytest.raises(ValueError, match="0 were written"):
        store._replace(pa.RecordBatchReader.from_batches(_schema(4), iter(())), 5)

    # And the ordinary path stays consistent: the two ways of asking agree.
    store.add({"a": [1.0, 0.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0, 0.0]})
    target = VectorStore(4)
    target.load_from(store, [("a", "one"), ("b", "two")])
    assert len(target) == target._table.count_rows() == len(target.keys()) == 2
