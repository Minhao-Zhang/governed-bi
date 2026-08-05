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
