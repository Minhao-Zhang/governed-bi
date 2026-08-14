"""Vector storage in LanceDB (columnar, exact cosine over candidates).

One table, one width. Writes only on misses (warm start is inert under the
file watcher). Keys: asset id (index) or cache_key (persistent cache).
"""


from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping, Sequence
from pathlib import Path

import lancedb
import pyarrow as pa
from lancedb.expr import col
from lancedb.index import BTree

from governed_bi.ports import Vector

from .semantic import check_query_vector

__all__ = ["MEMORY_URI", "VectorStore"]

#: Ephemeral store, isolated per connection: a second ``lancedb.connect("memory://")`` sees
#: no tables, so two indexes in one process cannot see each other's rows. Do not substitute
#: ``shared-memory://`` — ``open_table`` works across connections there while
#: ``list_tables()`` reports nothing.
MEMORY_URI = "memory://"

#: ``_distance`` slack tolerated before :func:`_similarity` refuses. A float32 vector scored
#: against itself measured ``-1.19e-07``; that is storage precision, and it is clamped.
#: Further out is not precision — it means a query omitted ``distance_type("cosine")``,
#: whose default is **squared L2** and returns plausible numbers in the thousands.
_RANGE_TOLERANCE = 1e-6

_VECTOR_COLUMN = "vector"
_KEY_COLUMN = "key"

#: Rows per batch when :meth:`VectorStore.load_from` streams (audit P2).
#:
#: **It buys peak, not net, and the first version of this comment claimed the opposite.** Measured
#: in isolated processes on the real store, a projected batched read peaks at +548.3 MB against
#: +548.2 MB for reading the whole table — *identical*, because Lance allocates the same buffers
#: either way. What batching is worth is the peak of the whole operation: +840 MB with a whole-table
#: read against +566 MB with this, since the read's buffers no longer have to coexist with the
#: write's. Every net megabyte came from writing through a ``RecordBatchReader`` instead of a
#: materialised table.
#:
#: Also not uniform: on the real store ``_batches()`` yields 17 ragged batches of 1 to 1,024 rows.
#:
#: Not a register knob: it changes no score and no arm would want it set differently.
_LOAD_BATCH_ROWS = 1024


def _schema(dimensions: int) -> pa.Schema:
    """Arrow schema for one width.

    The size argument on ``list_`` is what makes the column searchable: a plain
    ``list_(float32())`` is not a vector column, and ``search`` then reports *"There is no
    vector column in the data"*. float32, not float64: OpenAI embeddings are float32-native,
    the column is half the size, and disagreement with ``semantic.cosine`` measured 6.55e-08
    over 50 pairs at 3,072 dims. ``tests/retrieve/test_vector_store.py`` guards that.
    """
    return pa.schema([
        pa.field(_KEY_COLUMN, pa.string()),
        pa.field(_VECTOR_COLUMN, pa.list_(pa.float32(), dimensions)),
    ])


def _empty(dimensions: int) -> pa.Table:
    """An empty table of the right shape. Declared, not inferred: with no rows there is
    nothing to infer a width from, and the width is this store's invariant."""
    return pa.Table.from_arrays(
        [
            pa.array([], type=pa.string()),
            pa.FixedSizeListArray.from_arrays(pa.array([], type=pa.float32()), dimensions),
        ],
        schema=_schema(dimensions),
    )


def _similarity(distance: float) -> float:
    """``1 - cosine distance``, refusing anything that is not a cosine.

    ``_distance`` under ``cosine`` is in ``[0, 2]`` (identical 0.0, orthogonal 1.0, opposite
    2.0), so this is exact. The refusal is here because the metric is a per-query argument
    LanceDB validates against nothing: omit it and you get squared L2, silently.
    """
    similarity = 1.0 - distance
    if not -1.0 - _RANGE_TOLERANCE <= similarity <= 1.0 + _RANGE_TOLERANCE:
        raise ValueError(
            f"vector store returned {distance!r}, which is not a cosine distance — "
            "the most likely cause is a query that did not ask for distance_type('cosine')"
        )
    return min(1.0, max(-1.0, similarity))


class VectorStore:
    """Same-width vectors under string keys, searched by exact cosine.

    One table, named for its width, so 1,536 and 3,072 cannot collide in one directory —
    which is what makes a single per-model cache correct for ``text-embedding-3-large``
    at both of its widths.
    """

    def __init__(self, dimensions: int, *, uri: str | Path = MEMORY_URI) -> None:
        if int(dimensions) < 1:
            raise ValueError(f"dimensions must be positive, got {dimensions!r}")
        self._dimensions = int(dimensions)
        self._name = f"d{self._dimensions}"
        self._uri = str(uri)
        self._db = lancedb.connect(self._uri)
        try:
            table = self._db.open_table(self._name)
        except ValueError:
            table = self._db.create_table(
                self._name, _empty(self._dimensions), schema=_schema(self._dimensions)
            )
        stored = table.schema.field(_VECTOR_COLUMN).type
        if getattr(stored, "list_size", None) != self._dimensions:
            # Refuse rather than rebuild: rows written under a name that does not describe
            # them are a bug, and silently replacing them hides how they got there.
            raise ValueError(
                f"{self._uri}/{self._name} holds {stored} but this store declares "
                f"{self._dimensions}"
            )
        self._table = table
        self._rows = table.count_rows()
        self._opened_with = self._rows
        self._written = 0
        #: ``(model, dimensions)`` pairs whose embedding space has been checked against these
        #: rows, so ``build_index``'s canary costs one call per store rather than one per build.
        #:
        #: **Held on the store and not in a module-level set** because :data:`MEMORY_URI` gives
        #: every ephemeral store the same uri, so a process-wide key would let one in-memory store
        #: answer for another — and answering "already verified" for a store nobody has looked at
        #: is the failure the check exists to prevent.
        #:
        #: **Private, with no setter that clears it.** It shipped public for one commit, which put a
        #: writable off-switch on a refusal in a repository whose thesis is that governance is the
        #: absence of a channel: ``store._space_verified.add(...)`` and the check never runs again.
        self._space_verified: set[tuple[str, int]] = set()

    # ── identity and reporting ────────────────────────────────────────────────

    def space_is_verified(self, identity: tuple[str, int]) -> bool:
        """Whether ``(model, dimensions)`` has already been checked against these rows."""
        return identity in self._space_verified

    def mark_space_verified(self, identity: tuple[str, int]) -> None:
        """Record that ``(model, dimensions)`` matched these rows. **Add only, never clear.**

        Nothing removes an entry, and ``_replace`` deliberately does not either: a verdict about
        rows that were deleted is stale, but ``_replace`` is called once per store, on a fresh one,
        before it is ever verified. If that ever stops being true this is where it breaks.
        """
        self._space_verified.add(identity)

    @property
    def uri(self) -> str:
        """Where these rows are. Named in the mixed-space refusal, because "delete the store"
        is not actionable advice without it — and :data:`MEMORY_URI` says "nothing to delete"."""
        return self._uri

    @property
    def dimensions(self) -> int:
        """Vector width. Part of every cache key this store is addressed by."""
        return self._dimensions

    @property
    def opened_with(self) -> int:
        """Rows already present at open. Makes a cache hit reportable rather than assumed."""
        return self._opened_with

    @property
    def written(self) -> int:
        """Rows this process added. Zero means the run wrote no bytes — the property the
        ``langgraph dev`` reload loop depends on."""
        return self._written

    def __len__(self) -> int:
        return self._rows

    # ── filling ───────────────────────────────────────────────────────────────

    def keys(self) -> list[str]:
        """Every key, **without reading a single vector** — which this now honours.

        It said exactly that while calling ``self._table.to_arrow()``, which materialises every
        column. Measured on the real 14,613-row store: the projected scan below returns the same
        keys in **1.77 MB and 0.010 s** against **181.3 MB and 0.139 s** for the whole table, and a
        2 ms sampler catches the full read as a **+421 MB** working-set transient, because Arrow's
        buffers are off-heap and the copy is not in place. ``missing`` calls this once per build.

        ``search()`` with no query vector is LanceDB's plain scan; ``select`` is the projection.
        ``to_lance().to_table(columns=...)`` would also work and needs ``pylance``, which is not a
        dependency here.
        """
        return (
            self._table.search()
            .select([_KEY_COLUMN])
            # **No `limit`.** An unlimited scan already returns every row, so a limit could only
            # ever truncate — and `missing()` depends on this list being complete, so a truncated
            # one turns a cache hit into a miss. `limit(self._rows)` was the first version and it
            # coupled completeness to a cached integer for no gain. `limit(0)` in LanceDB means
            # *unlimited*, which is the trap on the other side.
            .to_arrow()
            .column(_KEY_COLUMN)
            .to_pylist()
        )

    def missing(self, keys: Sequence[str]) -> list[str]:
        """Which of ``keys`` this store does not hold, in the order given, deduplicated.

        Diffed in Python, not by an ``IN`` predicate: a cache key **contains the whole summary
        text**, so this corpus's **13,189** distinct keys make a **1.53 MB** SQL literal to parse
        per build, against 0.010 s to read the key column for 14,613 rows. (That sentence read
        "8,035 of them is ~1.6 MB" until review priced it: 8,035 keys at this corpus's mean key
        length is ~0.97 MB, so the two halves disagreed.)
        """
        present = set(self.keys())
        out: list[str] = []
        seen: set[str] = set()
        for key in keys:
            if key not in present and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    def add(self, items: Mapping[str, Vector]) -> None:
        """Upsert ``items``. **Call with the miss set or not at all**: this writes bytes even
        when every row is already identical, which the reload loop depends on not happening.

        ``merge_insert`` and not ``Table.add``: LanceDB has no unique constraint, and adding
        an existing key silently produces two rows.
        """
        if not items:
            return
        keys: list[str] = []
        flat: list[float] = []
        for key, vector in items.items():
            if len(vector) != self._dimensions:
                raise ValueError(
                    f"vector for {key!r} is {len(vector)} wide but this store declares "
                    f"{self._dimensions}"
                )
            if not any(vector):
                # A stored zero vector is silently dropped from a cosine result — three
                # rows in, two out, no error and no NaN — where `semantic.cosine` raises.
                # Refusing at write time keeps that a loud failure rather than a candidate
                # that was never scored.
                raise ValueError(f"refusing an all-zero vector for {key!r}")
            keys.append(str(key))
            flat.extend(vector)
        rows = pa.Table.from_arrays(
            [
                pa.array(keys, type=pa.string()),
                pa.FixedSizeListArray.from_arrays(
                    pa.array(flat, type=pa.float32()), self._dimensions
                ),
            ],
            schema=_schema(self._dimensions),
        )
        (
            self._table.merge_insert(_KEY_COLUMN)
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(rows)
        )
        self._rows = self._table.count_rows()
        self._written += len(keys)

    def load_from(self, source: VectorStore, pairs: Sequence[tuple[str, str]]) -> None:
        """Replace this store's rows with ``source``'s, re-keyed by ``pairs``, **streaming**.

        ``pairs`` is ``(source key, key here)`` — cache key to asset id — a sequence rather
        than a mapping because two assets may share a summary and so one cache entry.

        **Arrow in, Arrow out, one batch at a time** (audit P2). This was
        ``source.to_arrow()`` — the whole persistent cache — followed by a full-permutation
        ``.take()`` and a materialised table handed to ``create_table``: three copies of the
        payload alive at once, once per index build, which made it the largest allocation in the
        tree. Measured on the real 14,613-row store, one arm per process, 179.6 MB payload:

        ===================  ==========  =========
        private commit       before      after
        ===================  ==========  =========
        net                  +944 MB     +318 MB
        peak                 +1,473 MB   +566 MB
        ===================  ==========  =========

        Net amplification 5.3x down to 1.8x. **Peak amplification is 3.1x, not 1.8x**, and the two
        should not be quoted as one number.

        **The peaks above are the OS counters**, ``PeakPagefileUsage`` and ``PeakWorkingSetSize``,
        which Windows maintains exactly. A first version of this table read them from a 50 ms
        sampler and undershot by 25% before and 40% after, and then explained a colleague's larger
        figure away as *their* sampling artefact. It was not: a transient peak is not a function of
        the sampler when the OS is tracking it. Sample only when nothing else will tell you.

        **Which change earned it:** the ``RecordBatchReader`` write, not the batched read. Keeping
        ``source.to_arrow()`` and streaming only the write measures +311 MB net — the whole saving.
        Batching the read is worth peak alone, +840 MB down to +566 MB.

        **No ``IN`` predicate**, deliberately. Filtering the source to the wanted keys would
        save 9% of the read on this corpus — it needs 91% of the store — while building ~1.6 MB
        of SQL out of keys that contain whole summaries, the cost :meth:`missing` refuses to
        pay. Streaming makes the extra rows free in memory, so they are skipped in Python.

        **Membership is checked up front, against :meth:`keys`.** The check used to fall out of
        the ``row_of`` lookup, which a streaming writer cannot do: it would raise from inside the
        generator, after ``create_table`` had begun consuming batches. ``keys()`` is 1.77 MB, so
        the front is the cheap place for it.

        **It is defence in depth and not a correctness requirement, which is not what this said at
        first.** Review checked the late-raise case: ``create_table`` is transactional in LanceDB
        0.36, so a generator raising after the last batch leaves the target untouched on both
        ``memory://`` and disk. So the argument for checking up front is a clear error message and
        not depending on that transactionality — not "otherwise a partial table is written", which
        was asserted in four places without being tested.

        An absent key is a sequencing bug — the caller embeds the miss set first — and skipping it
        would build an index quietly short of vectors with the semantic channel reporting ``ran``
        over part of it.

        Rows come out in **source order**, not ``pairs`` order. Nothing depends on it: this is
        a keyed store, and ``semantic_search`` sorts by ``(-score, id)`` itself.
        """
        if source.dimensions != self._dimensions:
            raise ValueError(
                f"source store is {source.dimensions} wide, this one is {self._dimensions}"
            )
        if not pairs:
            self._replace(_empty(self._dimensions), 0)
            return

        #: source key -> every key here that wants it, in ``pairs`` order.
        wanted: dict[str, list[str]] = {}
        for source_key, key in pairs:
            wanted.setdefault(str(source_key), []).append(str(key))

        absent = sorted(set(wanted) - set(source.keys()))
        if absent:
            raise KeyError(
                f"{absent[0]!r} is not in the source store"
                + (f" (and {len(absent) - 1} more)" if len(absent) > 1 else "")
            )

        schema = _schema(self._dimensions)

        def rekeyed() -> "Iterator[pa.RecordBatch]":
            for batch in source._batches():
                take: list[int] = []
                out: list[str] = []
                for row, source_key in enumerate(batch.column(_KEY_COLUMN).to_pylist()):
                    for key in wanted.get(source_key, ()):
                        take.append(row)
                        out.append(key)
                if not out:
                    continue
                yield pa.record_batch(
                    [
                        pa.array(out, type=pa.string()),
                        batch.column(_VECTOR_COLUMN).take(pa.array(take, type=pa.int64())),
                    ],
                    schema=schema,
                )

        self._replace(
            pa.RecordBatchReader.from_batches(schema, rekeyed()), len(pairs)
        )

    def _batches(self, *, rows: int = _LOAD_BATCH_ROWS) -> "Iterator[pa.RecordBatch]":
        """Every row, projected to the two real columns, ``rows`` at a time.

        ``rows`` is the memory knob for :meth:`load_from`: at 3,072 dimensions a 1,024-row batch
        is ~12.6 MB, against 181 MB for the whole table.
        """
        yield from (
            self._table.search()
            .select([_KEY_COLUMN, _VECTOR_COLUMN])
            .to_batches(rows)
        )

    def to_arrow(self) -> pa.Table:
        """Every row, as Arrow. 0.139 s for 14,613 × 3,072 — and 181 MB, so prefer
        :meth:`keys` when only the keys are wanted."""
        return self._table.to_arrow()

    def _replace(self, rows: pa.Table | pa.RecordBatchReader, count: int) -> None:
        # **A fresh connection per overwrite.** Overwriting a table on a *retained* connection
        # leaks committed pages: 43.9 MB per call at a 12.3 MB payload over 200 iterations, linear
        # and independent of how much was written. **About a quarter of it is commit-only** — working
        # set grows 12.4 MB per call beside it — so an RSS tool sees some of this and not all. An
        # earlier version of this comment said working set stays flat. It does not.
        #
        # **The scope of that, honestly, because the first version of this comment got it wrong.**
        # `build_index` constructs a fresh `VectorStore` before every `load_from`, so `_replace`
        # runs exactly once per store and nothing in the tree retains one across calls. Measured in
        # isolated processes on the real path: 0.226 MB per build before this line, 0.201 MB after —
        # inside the noise. The comment here claimed ~47 GB over a 1,351-question run, which
        # measured a loop no caller performs. Kept because the mechanism is real and the day
        # something does retain a store, this is where it would bite; the saving is not the reason.
        #
        # Safe because this method replaces the table wholesale. For ``memory://`` a new connection
        # sees no tables at all, which is precisely what "overwrite" means here; for a file-backed
        # store the reconnect is a directory open and leaves the superseded Lance version on disk.
        self._db = lancedb.connect(self._uri)
        self._table = self._db.create_table(
            self._name, rows, schema=_schema(self._dimensions), mode="overwrite"
        )
        # **Counted from the table, not from the caller's promise** — a regression the streaming
        # rewrite introduced and review caught. `rows` may be a `RecordBatchReader`, so what is
        # written is decided by a generator while `count` was passed in before it ran: a reader
        # yielding nothing left `len(store) == 5` against a table holding 0, and `search`'s
        # `limit = self._rows` then returned a subset of the rows it did have. `add` already
        # counted this way, so `count` was a *second* writer of one field — the defect this
        # repository keeps paying for.
        written = self._table.count_rows()
        if count != written:
            raise ValueError(
                f"{self._uri}/{self._name}: caller said {count} row(s) and {written} were written. "
                "A partial write is not a store this process can reason about, because `keys()` "
                "and `missing()` treat the row set as complete."
            )
        self._rows = written
        self._written += written
        if count:
            # A **scalar** index on the key column — not a vector one, see `search`. The
            # candidate prefilter is a large `IN`; without this LanceDB scans every row to
            # evaluate it (152 ms -> 14 ms, 0.01 s to build).
            self._table.create_index(_KEY_COLUMN, config=BTree())

    # ── searching ─────────────────────────────────────────────────────────────

    def search(
        self, query: Vector, *, keys: Collection[str] | None = None
    ) -> list[tuple[str, float]]:
        """Cosine similarity of every row in ``keys`` against ``query``. Unordered.

        Returns ``(key, similarity)`` on the same scale as
        :func:`~governed_bi.retrieve.semantic.cosine`. **The caller sorts**: LanceDB breaks
        ties by insertion order, so two indexes built from the same assets in a different
        order disagree, and the ranking contract is that ties break by asset id.

        **No vector index is built, so this is exact.** LanceDB creates none by default but
        uses one automatically the moment it exists — on 13,968 × 3,072 an ``IvfPq`` index
        shared **one** of the brute-force top twenty, silently. Brute force is 190-270 ms.
        An approximate index must arrive as a measured knob, never as a default.

        The candidate restriction must stay a **prefilter** (LanceDB's default): with
        ``prefilter=False`` the same k=1000 filter and ``limit(20)`` returned 3 rows,
        because it filters the top twenty of the whole table.

        Safe to call concurrently on one store, which the pooled eval driver needs: it
        mutates nothing on ``self``, and 64 searches across 8 threads over one 2,000-row
        store matched the serial results, filtered and unfiltered.
        """
        check_query_vector(query, width=self._dimensions)
        if self._rows == 0:
            return []
        wanted: set[str] | None = None
        if keys is not None:
            wanted = {str(k) for k in keys}
            if not wanted:
                return []
        builder = self._table.search(list(query), vector_column_name=_VECTOR_COLUMN)
        builder = builder.distance_type("cosine")
        if wanted is not None and len(wanted) < self._rows:
            # `col(...).isin(...)` and never an f-string: an id containing an apostrophe
            # ends the SQL literal, and `asset_id IN ('o'brien.sales')` fails to tokenise.
            builder = builder.where(col(_KEY_COLUMN).isin(sorted(wanted)))
            limit = len(wanted)
            wanted = None
        else:
            # A candidate set covering the table is cheaper scanned than filtered: at
            # k = 13,968 of 13,968 the `IN` costs 451 ms against 244 ms for the scan.
            # Python then drops the non-candidates.
            limit = self._rows
        rows = (
            builder.limit(limit)
            # `_distance` is named explicitly: selecting without it warns today and stops
            # including it in a future release.
            .select([_KEY_COLUMN, "_distance"])
            .to_list()
        )
        out: list[tuple[str, float]] = []
        for row in rows:
            key = str(row[_KEY_COLUMN])
            if wanted is not None and key not in wanted:
                continue
            out.append((key, _similarity(float(row["_distance"]))))
        return out
