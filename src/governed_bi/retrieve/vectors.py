"""Vector storage in LanceDB (columnar, exact cosine over candidates).

One table, one width. Writes only on misses (warm start is inert under the
file watcher). Keys: asset id (index) or cache_key (persistent cache).
"""


from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
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

    # ── identity and reporting ────────────────────────────────────────────────

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
        """Every key, without reading a single vector."""
        return self._table.to_arrow().column(_KEY_COLUMN).to_pylist()

    def missing(self, keys: Sequence[str]) -> list[str]:
        """Which of ``keys`` this store does not hold, in the order given, deduplicated.

        Diffed in Python, not by an ``IN`` predicate: a cache key **contains the whole
        summary text**, so 8,035 of them is ~1.6 MB of SQL to parse per build against
        0.01 s to read the key column for 13,968 rows.
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
        """Replace this store's rows with ``source``'s, re-keyed by ``pairs``.

        ``pairs`` is ``(source key, key here)`` — cache key to asset id — a sequence rather
        than a mapping because two assets may share a summary and so one cache entry.

        **Arrow in, Arrow out**: vectors are taken by row index and never decoded, because
        materialising 13,968 × 3,072 as Python floats is 1.7 GB. ``source`` is read whole,
        since filtering it by key needs the ``IN`` predicate :meth:`missing` refuses.
        """
        if source.dimensions != self._dimensions:
            raise ValueError(
                f"source store is {source.dimensions} wide, this one is {self._dimensions}"
            )
        if not pairs:
            self._replace(_empty(self._dimensions), 0)
            return
        arrow = source.to_arrow()
        row_of = {key: i for i, key in enumerate(arrow.column(_KEY_COLUMN).to_pylist())}
        take: list[int] = []
        keys: list[str] = []
        for source_key, key in pairs:
            index = row_of.get(source_key)
            if index is None:
                # The caller embeds the miss set first, so an absent key is a sequencing bug,
                # not a cache miss. Skipping would build an index quietly short of vectors,
                # with the semantic channel reporting `ran` over part of it.
                raise KeyError(f"{source_key!r} is not in the source store")
            take.append(index)
            keys.append(str(key))
        vectors = arrow.column(_VECTOR_COLUMN).take(pa.array(take, type=pa.int64()))
        self._replace(
            pa.Table.from_arrays(
                [pa.array(keys, type=pa.string()), vectors], schema=_schema(self._dimensions)
            ),
            len(keys),
        )

    def to_arrow(self) -> pa.Table:
        """Every row, as Arrow. 0.17 s for 13,968 × 3,072 against 16.4 s for the JSON."""
        return self._table.to_arrow()

    def _replace(self, rows: pa.Table, count: int) -> None:
        self._table = self._db.create_table(
            self._name, rows, schema=_schema(self._dimensions), mode="overwrite"
        )
        self._rows = count
        self._written += count
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
