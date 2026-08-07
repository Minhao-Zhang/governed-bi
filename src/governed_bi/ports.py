"""Every Protocol in the system. Zero implementations. stdlib only.

Ports live at the bottom of the import graph so pure code can name a capability
without importing an adapter. Single-adapter seams are rejected (no ChatModel /
Tracer / Grader / Redactor / Checkpointer / Clock ports — adapters or values
elsewhere satisfy the need).

**The two-adapter rule is stated as a rule and was not one** (audit §10). This header
said "Every port here has at least two adapters", and of the five ports declared, three
had **zero** — the adapter files each named did not exist. ``record/jsonl_sink.py``,
``record/sqlite_sink.py``, ``corpus/yaml_store.py``, ``corpus/memory_store.py``,
``model/bedrock_embedder.py`` and ``serve/interrupt_responder.py`` were all fictional.

Two ports were deleted rather than documented: ``Sink`` (see below) and ``Responder``,
which had no implementation anywhere — the HITL path is
``langgraph.types.interrupt`` in ``serve/tools.ask_user`` and ``serve/resume.py``, not an
object satisfying a protocol. Every ``Adapters:`` line below now names a file that exists,
and where a port has one adapter it says so instead of claiming two.
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
    (``model/bedrock_embedder.py`` was named here and does not exist; ``pyproject.toml``
    records that the ``bedrock`` extra was deliberately removed.)

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

    Adapter: ``corpus/store.py`` — **one**, not the two this said. ``corpus/yaml_store.py``
    and ``corpus/memory_store.py`` never existed, so the in-memory adapter "for the pooled
    eval driver" was a plan recorded as a fact. A single-adapter port is a seam this module's
    own header rejects; it stays because ``corpus/store.py`` is the only reader of the corpus
    tree and the protocol is what keeps ``eval/`` from importing it, but the rule is broken
    here and saying so is the point.
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


# **``Sink`` was here and is gone** (audit §8.1 / §10). It declared "every record is redacted
# before write" and named two adapters — ``record/jsonl_sink.py`` and ``record/sqlite_sink.py``.
# There is no ``record/`` package and there never was; the port had no implementation, so the
# guarantee in its docstring described nothing.
#
# What actually writes records is ``api/trace_store.append_turn``, and it writes the question,
# the answer and the whole record verbatim to ``runs/serve/<date>.jsonl``. That is now what the
# documentation says, because this is a local-first tool and the log is the user's own
# transcript on their own disk. A port promising a control that no adapter implements is worse
# than no port: it is where a reader stops looking.


# **``Responder`` was here and is gone** (audit §10). It declared ``answer(question) -> str``
# and named ``serve/interrupt_responder.py`` plus an "eval simulated responder"; neither exists,
# and nothing in the repository implements the protocol or annotates against it.
#
# It is not a seam this system has. A clarification is ``langgraph.types.interrupt`` raised
# inside ``serve/tools.ask_user``, and the answer comes back through
# ``serve/resume.resume_clarification`` — an interrupt-resume protocol, not a callable object.
# The rules the docstring carried are real and live where they are enforced:
# ``resume_authorised`` for the identity binding (ADR 0006 §8), and ``serve/resume.py`` for not
# minting human provenance.
