"""Every Protocol in the system, and the value types that cross one. stdlib only.

Ports live at the bottom of the import graph so pure code can name a capability
without importing an adapter. Single-adapter seams are rejected (no ChatModel /
Tracer / Grader / Redactor / Checkpointer / Clock ports — adapters or values
elsewhere satisfy the need).

Every ``Adapters:`` line below must name files that exist, and every port left here has at
least two of them. One adapter is a seam nothing can move, so the adapter list doubles as the
evidence that each port earns its declaration.

**"Zero implementations" was the rule until ADR 0012 and is now narrower: no port is
implemented here, and the only classes with bodies are the frozen values a port's own
signature has to name.** ``AccessPolicy.grant_for`` returns a :class:`Grant`; a Protocol
cannot name a type from a later layer without inverting the import graph, so ``Grant`` and
``Principal`` live beside it. They carry validation and a digest and no behaviour — the
folding, the composition algebra and the enforcement all live in ``govern/access.py``,
where the identifier rules already are.
"""


from __future__ import annotations

import hashlib
from abc import abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, Sequence, runtime_checkable

__all__ = [
    "Vector",
    "Row",
    "ColumnInfo",
    "TableInfo",
    "Embedder",
    "Connector",
    "Principal",
    "Reach",
    "PredicateEnforcement",
    "RowPredicate",
    "Grant",
    "OPEN_GRANT",
    "AccessPolicy",
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


class Reach(str, Enum):
    """How wide a :class:`Grant`'s table authorization is.

    Two members and no third, because the third would be ``None``-means-everything —
    ADR 0006 G1's "absence is not permission", which ``check()`` already refuses for
    ``licensed``. Openness is a value you write down, never a value you omit.
    """

    #: Every table the turn licensed. The open grant, and the only one this repository ships.
    every_table = "every_table"
    #: Only the tables named in :attr:`Grant.tables`. An empty list authorizes nothing.
    listed = "listed"


class PredicateEnforcement(str, Enum):
    """Who applies a :class:`RowPredicate`. **There is deliberately no ``inject`` member.**

    ADR 0012 rejects rewriting a checked statement to add a ``WHERE`` clause: ``prepare()``
    is the only function that may produce an executable string, the ledger hashes what
    actually ran (ADR 0006 G4), and a predicate is semantically wrong under an outer join,
    inside a ``UNION`` arm, and against a CTE that shadows the table's name. A vocabulary
    that cannot spell the dangerous option is this repository's usual answer to "someone
    will implement it later".
    """

    #: This engine refuses any statement binding the table. The default, and the only
    #: enforcement it performs. Costly and safe.
    refuse = "refuse"
    #: The operator asserts the *database* enforces this predicate — a Postgres ``ROW LEVEL
    #: SECURITY`` policy on the connection role. This engine does **not** apply it and does
    #: not verify the claim; it records that the claim was made and does not refuse.
    database_role = "database_role"


@dataclass(frozen=True, slots=True)
class RowPredicate:
    """A row-level restriction on one table. See :class:`PredicateEnforcement`."""

    #: ``schema.table``, in the corpus's spelling. Folded where it is compared.
    table: str
    #: A SQL boolean expression, as the integrator wrote it. **Never parsed, never
    #: executed, never sent anywhere** by this repository — it exists so a fork can hand
    #: the same declaration to the database and to its own audit surface.
    expression: str
    enforcement: PredicateEnforcement = PredicateEnforcement.refuse

    def __post_init__(self) -> None:
        if not self.table.strip():
            raise ValueError("a row predicate must name a table")
        if not self.expression.strip():
            raise ValueError(f"the row predicate on {self.table!r} has an empty expression")
        object.__setattr__(self, "enforcement", PredicateEnforcement(self.enforcement))


@dataclass(frozen=True, slots=True)
class Principal:
    """The subject a turn is executed for.

    **This repository has exactly one**, ``govern/access.py::LOCAL_PRINCIPAL``, and since
    2026-08-13 nothing proves it: ``api/auth.py`` authenticates every caller unconditionally,
    so the subject is a constant rather than something a credential selects. It was a shared
    key before that, which was the same one principal by a different route. There is no user
    store, no identity provider and no tenant field, and ADR 0012 declines to add any. ``roles`` exists
    because an :class:`AccessPolicy` needs something to key on that is not the id itself —
    a fork that maps id → grant directly has a user store in its policy file.
    """

    id: str
    roles: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("a principal must have an id; '' is not an anonymous principal")
        object.__setattr__(self, "roles", frozenset(self.roles))


@dataclass(frozen=True, slots=True)
class Grant:
    """What one principal may see. The value an :class:`AccessPolicy` returns.

    **The default is deny.** ``Grant()`` authorizes no table, which is the fail-closed
    reading of an adapter that returned before deciding. :data:`OPEN_GRANT` is the opposite
    and has to be named.

    Keys are as the integrator wrote them; folding against the corpus's spelling happens in
    ``govern/access.resolve_grant``, which is the only place identifier rules live (ADR 0008
    D1). So ``Sales.Orders`` and ``sales.orders`` are the same table here, and the integrator
    does not have to know that.
    """

    reach: Reach = Reach.listed
    #: ``schema.table`` keys. Meaningful only when ``reach`` is :attr:`Reach.listed`.
    tables: frozenset[str] = frozenset()
    #: ``schema.table.column`` keys this principal may not read. **Applies under either
    #: reach**: grants are additive, denials are absolute (ADR 0012 §3).
    denied_columns: frozenset[str] = frozenset()
    row_predicates: tuple[RowPredicate, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reach", Reach(self.reach))
        object.__setattr__(self, "tables", frozenset(self.tables))
        object.__setattr__(self, "denied_columns", frozenset(self.denied_columns))
        object.__setattr__(self, "row_predicates", tuple(self.row_predicates))
        if self.reach is Reach.every_table and self.tables:
            raise ValueError(
                "a grant with reach=every_table also lists tables. One of the two is a "
                "mistake and guessing which would be guessing at an authorization: a reader "
                "cannot tell 'everything' from 'everything, and here is the list I meant'."
            )
        if any(not key.strip() for key in self.tables | self.denied_columns):
            raise ValueError("a grant key cannot be blank")
        seen: set[str] = set()
        for predicate in self.row_predicates:
            key = predicate.table.strip().lower()
            if key in seen:
                raise ValueError(
                    f"two row predicates name {predicate.table!r}. Which one applies is not a "
                    "question this repository may answer by sort order."
                )
            seen.add(key)

    @property
    def is_open(self) -> bool:
        """Whether this grant can change nothing: every table, no denial, no predicate."""
        return (
            self.reach is Reach.every_table
            and not self.denied_columns
            and not self.row_predicates
        )

    def digest(self) -> str:
        """A stable content digest, for whoever records the turn's security configuration.

        ADR 0006 §13 requires security configuration to reach the config hash, or two runs
        with different governance hash identically. The ``access_grant`` knob holds this value:
        it is ``Role.comparability``, so it enters ``config_hash_keys()``, and
        ``serve/session.py::_resolved_knobs`` resolves it by calling this method **on the
        policy**. Its register default is ``None`` and not the open grant's digest, because a
        default carrying one would publish "open" for a fork that shipped a restrictive
        policy — the ``agent_recursion_limit`` defect, in the security register. A null on a row
        therefore means "no policy was threaded", never "the grant was open".
        """
        parts = [f"reach={self.reach.value}"]
        parts += [f"table={key}" for key in sorted(self.tables)]
        parts += [f"deny={key}" for key in sorted(self.denied_columns)]
        parts += [
            f"predicate={p.table}|{p.enforcement.value}|{p.expression}"
            for p in sorted(self.row_predicates, key=lambda p: p.table)
        ]
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


#: The grant that authorizes everything and denies nothing — the behaviour this repository
#: had before ADR 0012 and still has by default. Named rather than spelled ``Grant(...)`` at
#: each site so "open" is one object every test can compare against.
OPEN_GRANT: Grant = Grant(reach=Reach.every_table)


@runtime_checkable
class AccessPolicy(Protocol):
    """Who may see what. **One method, on purpose.**

    Adapters: ``govern/access.py::OpenAccessPolicy`` (the default; authorizes everything and
    is what ships), ``govern/access.py::StaticRoleAccessPolicy`` (roles → grants from a TOML
    file). Two adapters, so the seam is a seam.

    The alternative shape — ``authorized_tables()`` / ``denied_columns()`` /
    ``row_predicate()`` as three getters — pushes four decisions onto every integrator:
    when to call them, how keys fold, what an empty answer means, and how two roles compose.
    Returning one :class:`Grant` per turn means the repository answers all four once.

    * Called **once per turn**, before the first statement. A policy that varies within a
      turn is a policy the ledger cannot describe.
    * A raising adapter is a wiring failure, not a refusal: it propagates. Fail-closed here
      would record "this query was unsafe" for "the policy file has a typo".
    * ``Grant()`` (the default) authorizes nothing. There is no return value meaning
      "no opinion".
    """

    def grant_for(self, principal: Principal) -> Grant:
        """This principal's authorization for the turn about to run."""
        ...


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
# ``resume_authorised`` for the identity binding (ADR 0006 §8), ``serve/resume.py`` for not
# minting human provenance.
