"""One table, one width. Writes only on misses. Keys are an asset id or a cache key."""


from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping, Sequence
from pathlib import Path
from typing import cast

import lancedb
import pyarrow as pa
from lancedb.expr import col
from lancedb.index import BTree
from lancedb.query import LanceVectorQueryBuilder

from governed_bi.ports import Vector

from .semantic import check_query_vector

__all__ = ["MEMORY_URI", "VectorStore"]

#: Ephemeral, isolated per connection. Do not substitute ``shared-memory://``:
#: ``open_table`` crosses connections there while ``list_tables()`` reports nothing.
MEMORY_URI = "memory://"

#: Slack before a cosine result is refused. Float32 scores a vector against itself
#: slightly below zero. Further out means squared L2, not cosine.
_RANGE_TOLERANCE = 1e-6

_VECTOR_COLUMN = "vector"
_KEY_COLUMN = "key"

#: Batch size for a streamed copy. Not a setting: it changes no score.
_LOAD_BATCH_ROWS = 1024


def _schema(dimensions: int) -> pa.Schema:
    """Arrow schema for one width.

    The size on ``list_`` is what makes the column searchable. float32 matches the embedder's native width.
    """
    return pa.schema([
        pa.field(_KEY_COLUMN, pa.string()),
        pa.field(_VECTOR_COLUMN, pa.list_(pa.float32(), dimensions)),
    ])


def _empty(dimensions: int) -> pa.Table:
    return pa.Table.from_arrays(
        [
            pa.array([], type=pa.string()),
            pa.FixedSizeListArray.from_arrays(pa.array([], type=pa.float32()), dimensions),
        ],
        schema=_schema(dimensions),
    )


def _similarity(distance: float) -> float:
    """``1 - cosine distance``. Refuse anything outside a cosine.

    The metric is a per-query argument the store does not check. Omit it and the result is squared L2.
    """
    similarity = 1.0 - distance
    if not -1.0 - _RANGE_TOLERANCE <= similarity <= 1.0 + _RANGE_TOLERANCE:
        raise ValueError(
            f"vector store returned {distance!r}, which is not a cosine distance — "
            "the most likely cause is a query that did not ask for distance_type('cosine')"
        )
    return min(1.0, max(-1.0, similarity))


class VectorStore:
    """Same-width vectors under string keys, one table per width so two widths cannot collide."""

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
            # Refuse rather than rebuild. Replacing misnamed rows would hide how they got there.
            raise ValueError(
                f"{self._uri}/{self._name} holds {stored} but this store declares "
                f"{self._dimensions}"
            )
        self._table = table
        self._rows = table.count_rows()
        self._opened_with = self._rows
        self._written = 0
        #: On this store. Every ephemeral store shares one uri, so a process-wide key collides.
        #: Add-only. A cleared entry would skip the space check.
        self._space_verified: set[tuple[str, int]] = set()

    def space_is_verified(self, identity: tuple[str, int]) -> bool:
        return identity in self._space_verified

    def mark_space_verified(self, identity: tuple[str, int]) -> None:
        """Record a match. Add only.

        ``_replace`` does not clear this: it runs on a fresh store, before any check.
        """
        self._space_verified.add(identity)

    @property
    def uri(self) -> str:
        return self._uri

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def opened_with(self) -> int:
        return self._opened_with

    @property
    def written(self) -> int:
        return self._written

    def __len__(self) -> int:
        return self._rows

    def keys(self) -> list[str]:
        return (
            self._table.search()
            .select([_KEY_COLUMN])
            # No limit: ``missing`` needs every key. ``limit(0)`` means unlimited.
            # Project the key column. A full table read would materialise every vector.
            .to_arrow()
            .column(_KEY_COLUMN)
            .to_pylist()
        )

    def missing(self, keys: Sequence[str]) -> list[str]:
        """Which of ``keys`` are absent, in order, deduplicated.

        Diffed in Python. A cache key holds the whole summary, so an ``IN`` list is a large SQL literal on every build.
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
        """Upsert ``items``. Write only the miss set: this writes bytes even when the rows match.

        ``merge_insert`` because there is no unique constraint, and adding an existing key silently produces two rows.
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
                # A stored zero is dropped from a cosine result with no error. Refuse at write.
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
        """Replace these rows from ``source``, re-keyed by ``pairs``, one batch at a time.

        ``pairs`` is a sequence because two assets may share a summary. Rows follow source order; the search sorts by
        score and id.
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

        # Checked here so the error is raised in this function. A late raise is still atomic.
        # No ``IN`` filter: summary-sized keys make a large SQL literal.
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
        """Projected rows, ``rows`` at a time. That is the memory bound for :meth:`load_from`."""
        yield from (
            self._table.search()
            .select([_KEY_COLUMN, _VECTOR_COLUMN])
            .to_batches(rows)
        )

    def to_arrow(self) -> pa.Table:
        return self._table.to_arrow()

    def _replace(self, rows: pa.Table | pa.RecordBatchReader, count: int) -> None:
        # A fresh connection per overwrite. A retained connection leaks committed pages.
        self._db = lancedb.connect(self._uri)
        self._table = self._db.create_table(
            self._name, rows, schema=_schema(self._dimensions), mode="overwrite"
        )
        # Counted from the table. A caller's ``count`` can disagree with a reader.
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
            # A scalar index on the key. Without it the candidate ``IN`` scans every row.
            self._table.create_index(_KEY_COLUMN, config=BTree())

    def search(
        self, query: Vector, *, keys: Collection[str] | None = None
    ) -> list[tuple[str, float]]:
        """Exact cosine over ``keys``. Unordered; the caller sorts. Safe to call concurrently: it mutates nothing here.

        No vector index is built. If one exists the store uses it, and an approximate index disagreed with brute force.
        Keep the candidate restriction a prefilter; filtering after the limit keeps only the top of the whole table.
        """
        check_query_vector(query, width=self._dimensions)
        if self._rows == 0:
            return []
        wanted: set[str] | None = None
        if keys is not None:
            wanted = {str(k) for k in keys}
            if not wanted:
                return []
        # `search` is typed as the base builder; a vector query returns the vector one.
        builder = cast(
            LanceVectorQueryBuilder,
            self._table.search(list(query), vector_column_name=_VECTOR_COLUMN),
        )
        builder = builder.distance_type("cosine")
        if wanted is not None and len(wanted) < self._rows:
            # ``isin``, never an f-string. An apostrophe in an id ends the SQL literal.
            builder = builder.where(col(_KEY_COLUMN).isin(sorted(wanted)))
            limit = len(wanted)
            wanted = None
        else:
            # A candidate set covering the table is cheaper scanned than filtered.
            limit = self._rows
        rows = (
            builder.limit(limit)
            # ``_distance`` is named. Selecting without it warns today and drops it later.
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
