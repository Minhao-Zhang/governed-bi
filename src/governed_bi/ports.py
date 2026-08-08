"""Every Protocol in the system. Zero implementations. stdlib only.

Ports live at the bottom of the import graph so pure code can name a capability
without importing an adapter. Single-adapter seams are rejected (no ChatModel /
Tracer / Grader / Redactor / Checkpointer / Clock ports — adapters or values
elsewhere satisfy the need).

Every ``Adapters:`` line below must name a file that exists; where a port has one adapter
it says so rather than claiming two.
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

    Adapters: ``model/openai_embedder.py``, ``model/deterministic_embedder.py``.

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

    # No ``sample_values``: a port method taking identifiers and building SQL from them has to
    # escape them (``physical_name`` is unconstrained in content, ``corpus/identity.slug``) and
    # reaches the database without a ledger row. ``serve/fetch`` builds that statement as a
    # syntax tree and runs it through ``prepare()``. One path to the database, and it is
    # ``execute``.


@runtime_checkable
class CorpusStore(Protocol):
    """Where a corpus lives.

    Adapter: ``corpus/store.py`` — one, breaking this module's own no-single-adapter rule.
    It stays because the protocol is what keeps ``eval/`` from importing the only reader of
    the corpus tree.
    """

    def load(self, *, schemas: Sequence[str]) -> tuple[Sequence[Mapping[str, Any]], Sequence[str]]:
        """Load raw asset mappings for ``schemas``. Returns ``(assets, problems)``."""
        ...

    def write(self, asset: Mapping[str, Any]) -> None:
        """Persist one asset. Path components validated before any filesystem access."""
        ...

    def content_hash(self, *, schemas: Sequence[str]) -> str:
        """Digest of the stored content for ``schemas``.

        Paths relative and sorted, so a staging directory cannot leak into the digest. A file
        that exists but cannot be read is named in the digest **without its bytes**: skipping
        it silently made an unreadable corpus hash identically to one never written.
        """
        ...


# There is no ``Sink`` port and no ``record/`` package. Records are written by
# ``api/trace_store.append_turn``, verbatim and unredacted, to ``runs/serve/<date>.jsonl``.
#
# There is no ``Responder`` port either. Clarification is ``langgraph.types.interrupt`` raised
# in ``serve/tools.ask_user`` and resumed by ``serve/resume.resume_clarification`` — an
# interrupt-resume protocol, not a callable. Its rules live where they are enforced:
# ``resume_authorised`` for the identity binding (ADR 0006 §8), ``serve/resume.py`` for not
# minting human provenance.
