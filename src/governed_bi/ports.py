"""Every Protocol in the system, and the value types that cross one. stdlib only.

Ports live at the bottom of the import graph so pure code can name a capability
without importing an adapter. Single-adapter seams are rejected (no ChatModel /
Tracer / Grader / Redactor / Checkpointer / Clock ports — adapters or values
elsewhere satisfy the need).

Every ``Adapters:`` line below must name files that exist, and every port left here has at
least two of them. One adapter is a seam nothing can move, so the adapter list doubles as the
evidence that each port earns its declaration.
"""


from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, Sequence, runtime_checkable

__all__ = [
    "Vector",
    "Row",
    "ColumnInfo",
    "TableInfo",
    "Embedder",
    "Connector",
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
    ``model/proxy_embedder.py`` (the three ``model/provider.py::embedder`` builds) and
    ``model/deterministic_embedder.py``. All four subclass ``model/embedder.py::BaseEmbedder``,
    which subclasses **this**.

    **This is the seam's only declaration.** ``BaseEmbedder`` used to restate ``model``,
    ``dimensions`` and ``embed`` as a second set of abstract members, so "what must an embedder
    do" had two answers and a change had two places to land. It now inherits them, which is why
    the three below are ``@abstractmethod`` where no other port here is: ``Protocol`` members
    with a ``...`` body are inherited as *concrete* methods returning ``None``, so without the
    decorator an adapter that forgot ``model`` would construct and report ``None`` as its cache
    key. Nothing is implemented here; explicit inheritance is legal because ``model`` is layer
    11 and may import layer 3, never the reverse — which is also why ``retrieve/`` and
    ``serve/`` annotate against this name and not against ``BaseEmbedder``.

    Still a Protocol, and still ``runtime_checkable``: test doubles satisfy it structurally
    without importing ``model/``, and ``isinstance`` holds for them.

    * ``embed`` returns one vector per input, in order, each of length ``dimensions``.
    * Callers must not pass empty/whitespace-only strings (adapters disagree on them).
    * ``model`` / ``dimensions`` are stable for the object's lifetime.
    * Rate limits and dead endpoints raise — never absorb into a low score.
    """

    @property
    @abstractmethod
    def model(self) -> str:
        """Provider-qualified model identity. Part of every cache key."""
        ...

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Vector width. Part of every cache key."""
        ...

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """Embed ``texts``, returning one vector each, in order.

        ``model/embedder.py::BaseEmbedder`` overrides this with the batching, blank-input and
        width checks every adapter shares, leaving each adapter only ``_embed_batch``.
        """
        ...


@runtime_checkable
class Connector(Protocol):
    """Read-only access to one datasource. Last hop before the database (ADR 0006 G2).

    Adapters: ``datasource/postgres.py``, ``datasource/sqlite.py``. Context-managed.
    Session: read-only + ``synchronize_seqscans = off`` (deterministic samples).
    There is no shared base class: each adapter applies its own ``max_rows`` by fetching
    ``max_rows + 1``. ``execute`` does no governance check — on the served path the only
    string it is handed is ``govern.pipeline.prepare()``'s output, passed by
    ``serve/fetch.py``, which is the sole serve-side caller. ``eval/harness.py`` and
    ``eval/oracle.py`` call it directly with ungoverned SQL on purpose: that is the
    measurement harness pricing a refusal and running gold, never a served turn.
    Driver exceptions propagate;
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

        ``truncated`` is derived from the adapter's own ``max_rows + 1`` fetch, so
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


# There was a ``CorpusStore`` port, deleted 2026-08-25. Its stated reason was that the Protocol
# "keeps ``eval/`` from importing the only reader of the corpus tree", and nothing ever typed
# against it: ``corpus/store.py`` exposes ``load`` and ``write`` as free functions that
# ``serve/session.py`` imports by name, and the digest was never in ``store.py`` at all — it is
# ``corpus/hash.py::corpus_content_hash``, which ``eval/feedback_import.py`` also imports
# directly, so the port's own signature had already drifted from the code it described.
# One adapter and no reader is a declaration, not a seam, which is the defect
# ``docs/open-work.md`` §3.10 names. Its three method docstrings were the only thing it carried,
# and each rule was already stated on the function that enforces it — error isolation on
# ``store.load``, path validation as a security control on ``store.write``, relative sorted paths
# and named-but-unread files on ``corpus_content_hash`` — so the port was a second copy of prose
# that would have drifted from the code the day one of them changed.
#
# There is no ``Sink`` port and no ``record/`` package. Records are written by
# ``api/graph_app.record_node`` onto ``ServeState.turns``, verbatim and unredacted, and the
# checkpointer persists it.
#
# There is no ``Responder`` port either. Clarification is ``langgraph.types.interrupt`` raised
# in ``serve/tools.ask_user`` and resumed by ``serve/resume.resume_clarification`` — an
# interrupt-resume protocol, not a callable. Its rules live where they are enforced:
# ``serve/clarification.py::parse_resume`` for what a reply means and when it fails closed.
