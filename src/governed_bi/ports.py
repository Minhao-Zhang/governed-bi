"""Every Protocol in the system. Zero implementations. stdlib only.

Ports live at the bottom of the import graph so pure code can name a capability
without importing an adapter. Every port here has at least two adapters;
single-adapter seams are rejected (no ChatModel / Tracer / Grader / Redactor /
Checkpointer / Clock ports — adapters or values elsewhere satisfy the need).
"""


from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

__all__ = [
    "Vector",
    "Row",
    "ColumnInfo",
    "TableInfo",
    "Embedder",
    "Connector",
    "CorpusStore",
    "Sink",
    "Responder",
]

#: Dense embedding as a plain list (stdlib-safe across the port boundary).
#: Adapters convert to/from Arrow internally; nothing crossing this port is pyarrow/numpy.
Vector = Sequence[float]

#: One result row. Values are whatever the driver returned; normalisation for
#: comparison is the grader's job, not the connector's.
Row = tuple[Any, ...]


class ColumnInfo(Protocol):
    """Catalog facts about one column, as the connector reports them."""

    name: str
    data_type: str
    nullable: bool
    primary_key: bool


class TableInfo(Protocol):
    """Catalog facts about one table."""

    name: str
    columns: Sequence[ColumnInfo]


@runtime_checkable
class Embedder(Protocol):
    """Turn text into vectors. Exposes ``model`` and ``dimensions`` for cache keys.

    Adapters: ``model/openai_embedder.py``, ``model/bedrock_embedder.py``,
    ``model/deterministic_embedder.py``.

    * ``embed`` returns one vector per input, in order, each of length ``dimensions``.
    * Callers must not pass empty/whitespace-only strings (adapters disagree on them).
    * ``model`` / ``dimensions`` are stable for the object's lifetime.
    * Rate limits and dead endpoints raise — never absorb into a low score.
    """

    @property
    def model(self) -> str:
        """Provider-qualified model identity. Part of every cache key."""
        ...

    @property
    def dimensions(self) -> int:
        """Vector width. Part of every cache key."""
        ...

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """Embed ``texts``, returning one vector each, in order."""
        ...


@runtime_checkable
class Connector(Protocol):
    """Read-only access to one datasource. Last hop before the database (ADR 0006 G2).

    Adapters: ``datasource/postgres.py``, ``datasource/sqlite.py``. Context-managed.
    Session: read-only + ``synchronize_seqscans = off`` (deterministic samples).
    Row limit is applied by the base class. ``execute`` does no governance check —
    only ``govern.pipeline`` may call it. Driver exceptions propagate;
    SQLite wraps "no such column" in ``OperationalError``. Production also needs
    a read-only DB role (read-only does not stop read-side exfiltration).
    """

    def __enter__(self) -> Connector: ...

    def __exit__(self, *exc: object) -> None: ...

    @property
    def dialect(self) -> str:
        """The sqlglot dialect name. Threaded into every parse and rewrite."""
        ...

    def execute(self, sql: str) -> tuple[Sequence[str], Sequence[Row], bool]:
        """Run ``sql``. Returns ``(columns, rows, truncated)``.

        ``truncated`` is derived from the base class's ``max_rows + 1`` limit, so
        "we hit the cap" is a fact the caller receives rather than infers from a
        row count it would have to know the cap to interpret.
        """
        ...

    def list_tables(self) -> Sequence[str]: ...

    def describe_table(self, name: str) -> TableInfo: ...

    # **``sample_values`` was removed, not fixed.** It was the one port method that took
    # identifiers and built SQL from them, so it had to escape them, and only the SQLite
    # adapter did — Postgres interpolated ``f'... FROM "{schema}"."{table}"'`` into a string
    # and ``physical_name`` is deliberately unconstrained in content (``corpus/identity.slug``).
    # Worse, it called ``execute`` itself, which is the method this port reserves for
    # ``govern.pipeline``, so the tool that used it reached the database through no layer and
    # wrote no ledger row.
    #
    # ``serve/fetch.distinct_values_statement`` builds that statement as a syntax tree and
    # ``serve/fetch.sample_rows`` runs it through ``prepare()`` like any other governed
    # statement. There is one path to the database now, and it is ``execute``.


@runtime_checkable
class CorpusStore(Protocol):
    """Where a corpus lives.

    Adapters: ``corpus/yaml_store.py``, ``corpus/memory_store.py``.
    In-memory adapter is for the pooled eval driver (avoid re-reading YAML per question).
    """

    def load(self, *, schemas: Sequence[str]) -> tuple[Sequence[Mapping[str, Any]], Sequence[str]]:
        """Load raw asset mappings for ``schemas``. Returns ``(assets, problems)``."""
        ...

    def write(self, asset: Mapping[str, Any]) -> None:
        """Persist one asset. Path components validated before any filesystem access."""
        ...

    def content_hash(self, *, schemas: Sequence[str]) -> str:
        """Digest of the stored content for ``schemas``.

        Paths relative and sorted, so a staging directory cannot leak into the
        digest. A file that exists but cannot be read is named in the digest
        **without its bytes** — skipping it silently made an unreadable corpus
        hash identically to one that was never written.
        """
        ...


@runtime_checkable
class Sink(Protocol):
    """Where records go. Write-only during a run.

    Adapters: ``record/jsonl_sink.py`` (concurrent append), ``record/sqlite_sink.py`` (export).
    Every record is redacted before write. ``append`` never raises into the serve path.
    ``drain`` is explicit — do not rely on ``atexit``.
    """

    def append(self, record: Mapping[str, Any]) -> None: ...

    def drain(self) -> None: ...


@runtime_checkable
class Responder(Protocol):
    """Answers a clarification question.

    Adapters: ``serve/interrupt_responder.py``, eval simulated responder.
    Answers outside this port must not mint human provenance. Resume is bound to
    caller identity (ADR 0006 §8 / ``resume_authorised``).
    """

    def answer(self, question: str) -> str: ...
