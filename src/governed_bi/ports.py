"""Every Protocol in the system. Zero implementations. stdlib only.

**Why they all live at the bottom rather than beside their adapters.** Pure
computation needs to be typed against a capability without importing anything
that can perform it — ``corpus.index`` must name ``Embedder`` without importing
an OpenAI client, or the memory layer drags a provider SDK into every test. A
port declared below its consumers makes that possible; a port declared beside its
adapter does not.

This inverts the hexagonal picture, and the inversion is worth stating: in the
usual diagram adapters are the outermost ring, but in an *import* graph they must
be importable by orchestration, so **adapters land in the middle and their ports
land at the bottom**. ``serve`` and ``eval`` are the outside.

**Every port here has at least two adapters.** One adapter means a hypothetical
seam; a single-adapter port is indirection. The rejected ports are listed at the
bottom of this docstring with the reason, because "why is there no X port" is a
question that will be asked again.

Rejected ports, and why:

``ChatModel``
    LangChain's ``BaseChatModel`` already *is* this port. v1 had
    ``llm/client.py`` + ``llm/langchain_client.py`` + ``llm/fake.py`` — three
    layers over someone else's abstraction. The one real requirement, that a test
    double **record** the messages and tool set it was handed (v1's fake
    discarded ``messages``, so the system prompt and the tool set could both have
    been emptied with a green suite), is a requirement on the double, not a
    reason for a new Protocol.

``Tracer``
    One adapter. LangSmith is the only tracer and is environment-configured.
    A port would also invite asserting trace metadata at a re-export, and the v1
    lesson is the opposite: assert it at the call site that threads it.

``Grader``
    One grader plus a committed byte-golden. A second adapter is how one quantity
    ends up with two definitions.

``Redactor``
    One redactor by mandate. v1 had two policies for one record and the
    anonymously-reachable sink used the weaker one.

``Checkpointer``
    Two adapters exist, but they are LangGraph's, satisfying LangGraph's
    interface. Wrapping them in ours is indirection over someone else's seam.

``Clock``
    One adapter. Determinism comes from passing a timestamp *value* down from the
    entry point, which is cheaper and makes the dependency visible in the
    signature.
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

#: A dense embedding. A plain list, not numpy: this type crosses into
#: ``register``-adjacent code that must import in a bare interpreter.
#:
#: Still true after the vector store became LanceDB, and it is a boundary rather than an
#: accident: ``retrieve/vectors.py`` speaks Arrow *internally* — a 13,968 × 3,072 table moved
#: as ``list[float]`` is the 1.7 GB that store exists to delete — and converts at its own edge.
#: Nothing that crosses this port, and nothing that reaches ``ServeState["query_vector"]``,
#: is a pyarrow or numpy object.
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
    """Turn text into vectors.

    **Why this is a port and ``ChatModel`` is not.** LangChain's ``Embeddings``
    abstraction lacks the two attributes every cache key in this system needs:
    the model identity and the width. That is not a stylistic gap — v1's vector
    cache omitted them, and because ``cosine`` returns ``0.0`` on a width
    mismatch instead of raising, a cross-model cache hit degraded routing to
    "nothing scores" with no error anywhere. This port exists to make those two
    facts part of the interface.

    Adapters: ``model/openai_embedder.py``, ``model/bedrock_embedder.py``,
    ``model/deterministic_embedder.py`` (the last is what makes ADR 0005's
    implementation steps 6–9 model-free, and it is a third adapter, not a
    courtesy fake).

    Interface, beyond the signatures:

    * ``embed`` returns one vector per input, in input order. A shorter or
      reordered result is a bug in the adapter, never something the caller
      reconciles.
    * Every returned vector has length ``dimensions``.
    * **Callers must not pass an empty or whitespace-only string.** The adapters
      disagree on it in a way that cannot be papered over: OpenAI accepts it and
      returns a vector that can score above zero and pollute a ranking; Bedrock
      Titan rejects it with ``ValidationException`` and takes the whole turn
      down. ``corpus.assets`` enforces non-empty ``summary`` upstream, and
      ``corpus.index`` asserts it again at build.
    * ``model`` and ``dimensions`` are stable for the lifetime of the object and
      belong in every cache key derived from it.

    Error modes: a rate limit or a dead endpoint **raises**. The caller
    (``retrieve.facets``) converts that into ``ChannelState.failed`` and records
    it — it must never be silently absorbed into a low score, which is how a
    rate-limited embedder published a schema-pick accuracy that re-measured 21
    points higher once quota was free.

    Performance: one call is one round trip for OpenAI and *one request per
    document* for Bedrock. Batching and caching are the caller's job;
    ``corpus.vectors`` owns both. This port does not cache.
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
    """Read-only access to one datasource. The last hop before the database.

    Adapters: ``datasource/postgres.py``, ``datasource/sqlite.py``. Both are
    production targets — SQLite is not merely a stand-in for Postgres, it is the
    engine the fixture corpus and the whole governance suite run against, and it
    fakes a schema namespace via ``ATTACH`` so ``schema.table`` resolves the same
    way.

    Interface, beyond the signatures:

    * **Context-managed.** v1 constructed a connector per probe and dropped it,
      leaving 131 unclosed SQLite handles across the suite — real file-descriptor
      pressure on a 69-schema pooled run.
    * Session settings are applied on open, under ``autocommit=True`` so they
      take effect immediately: ``default_transaction_read_only = on`` /
      ``PRAGMA query_only = ON``, plus ``synchronize_seqscans = off``.
      ``connection.read_only`` is **not** the enforcement path — it only shapes a
      ``BEGIN ... READ ONLY`` that never happens under autocommit.
    * ``synchronize_seqscans = off`` is not a performance setting. Postgres
      defaults it on, so an unordered ``LIMIT n`` returns different rows depending
      on what else is scanning the table; v1 observed the same column profiled as
      ``2018/8/5`` and ``2018/8/1`` in two runs. Since sample values render into
      the prompt, that made two arms differ for a reason unrelated to the
      intervention.
    * **The forced row limit is applied by the base class, not by adapters.** v1
      documented a gateway-wide cap and SQLite was the one path without it.
    * ``execute`` performs **no** governance check. It is the last hop;
      ``govern.pipeline`` is what may call it. That separation is the point of
      ADR 0006 G2, and every caller of this method is enumerated there.

    Error modes: driver exceptions propagate. Note the trap —
    ``OperationalError`` is **not** an infrastructure class, because SQLite
    wraps "no such column" in it, and classifying those as infrastructure hides
    wrong answers as crashes.

    Security: read-only here is belt-and-braces. **Production must also connect
    through a read-only database role** — an application bug should never be the
    last line, and ADR 0006 §2's function allowlist exists precisely because
    read-only does not stop read-side exfiltration (``pg_read_file``,
    ``table_to_xml``, ``dblink``).
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

    def sample_values(
        self, table: str, column: str, *, limit: int, schema: str | None = None
    ) -> Sequence[Any]:
        """Sample distinct values. **Deterministic**: ordered, and run under
        ``synchronize_seqscans = off``. See the note above on why.

        ``schema`` is part of the port because leaving it out did not make the concept go
        away — it made the Postgres adapter carry a private ``schema="public"`` default
        that no caller could see and none of them passed. On a pooled 57-schema lake every
        call became ``FROM "public"."<table>"`` and raised 42P01, so the one tool that
        could tell the analyst whether a column holds ``'CA'`` or ``'California'`` had
        never returned a row. An adapter over a namespace-free engine ignores it.
        """
        ...


@runtime_checkable
class CorpusStore(Protocol):
    """Where a corpus lives.

    Adapters: ``corpus/yaml_store.py``, ``corpus/memory_store.py``.

    **The in-memory adapter has a real second caller, not just tests.** The
    pooled eval driver holds up to 57 built corpora and must not re-read YAML per
    question — YAML parsing was measured at ~23% of an offline run's wall clock,
    and a 69-schema build re-reads the trees once per arm. That is what makes
    this a real seam rather than indirection over a filesystem.

    Interface, beyond the signatures:

    * ``load`` takes an **explicit manifest** of the schemas being loaded, never
      a directory listing. v1's shared corpus root was a cross-run contamination
      channel: a schema dropped from one attempt left its YAML behind and then
      competed as a router candidate for *every other schema's questions*,
      silently changing the routing problem's difficulty between two runs of the
      same set. Load fails on any mismatch between manifest and store.
    * ``load`` is **per-item error isolated and loud**. One truncated file must
      not discard a fully paid 69-schema build (v1's did), and a silent skip is
      worse than a failure — it turns "a corpus that lost half its assets" into
      "a corpus that merely looks small", and this project has already published
      a result on top of that.
    * ``write`` validates every path component at the boundary
      (``corpus.identity.validate_path_component``) because an asset's schema
      name becomes a directory name.

    Error modes: ``load`` returns the assets it could read plus a report of what
    it could not, and never raises for a bad item. ``write`` raises on an unsafe
    path component — that one is a security control, not a degradation.
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

    Adapters: ``record/jsonl_sink.py``, ``record/sqlite_sink.py``.

    The split is not stylistic. Up to 20 concurrent workers append during a run
    and append-to-JSONL is contention-free; twenty writers on one SQLite file is
    ``database is locked`` two hours in. SQLite is for export, after the fact.

    Interface, beyond the signatures:

    * **Every record passes through ``record.redact`` before it is written.** A
      sink that does not is a CI failure. v1 had two sinks for one record with
      different redaction policies and the anonymously-reachable one used the
      weaker.
    * ``append`` **never raises into the serve path.** A failed write is threaded
      back as a field on the record instead, so the answer states its own audit
      gap — refusing to answer because a log write failed trades a silent gap for
      an outage.
    * ``drain`` is explicit. Nothing relies on ``atexit``: exporters run on a
      background thread behind a hook that SIGTERM, ``os._exit`` and CI
      cancellation all bypass, so a short-lived process loses its final batch.
    """

    def append(self, record: Mapping[str, Any]) -> None: ...

    def drain(self) -> None: ...


@runtime_checkable
class Responder(Protocol):
    """Answers a clarification question.

    Adapters: ``serve/interrupt_responder.py`` (LangGraph ``interrupt`` — a real
    human), and the eval arm's simulated responder.

    Interface, beyond the signature:

    * An answer that did not come through this port **must not** be able to mint
      human provenance. v1's curator wrote ``{"status": "answered",
      "answered_by": "Jane Chen, Finance"}`` into a file it owned and that came
      out of the fold as ``source=human, status=certified``. The guard is a
      phase-boundary function in code, not a prompt instruction: *"the prompt
      telling the agent to write status: open is not a control. This is."*
    * A resume is bound to the caller's identity and rejects a mismatch. v1's
      process-global checkpointer let a guessable ``thread_id`` land on another
      caller's paused clarification, which embeds their question.
    """

    def answer(self, question: str) -> str: ...
