"""Serve graph state and reducers (ADR 0005 §3.2).

``usage`` uses ``operator.add`` and therefore accumulates across turns under a
checkpointer. Every :class:`UsageRecord` must carry ``turn_index``; ``stamp``
filters to the current turn when projecting the register. Do not treat the raw
channel as the per-turn cost list.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langgraph.graph.message import add_messages

from governed_bi.govern.guard import GuardVerdict
from governed_bi.govern.ledger import ExecutionRecord
from governed_bi.register.quantity import Measured

__all__ = [
    "RewriteResult",
    "NegativeVerdict",
    "FacetResult",
    "SchemaCrossing",
    "RetrievalResult",
    "NodeFailure",
    "Delivery",
    "UsageRecord",
    "Answer",
    "ServeState",
    "PathKind",
    "TERMINAL_PATH_KINDS",
    "RESET",
    "PER_TURN_RESET",
    "ACCUMULATING",
    "TURN_IDENTITY",
    "TEST_HOOKS",
    "merge_facets",
    "settle_path_kind",
    "settle_failure",
]


PathKind = Literal["refuse", "decline", "answered", "crashed"]

#: Path kinds that short-circuit remaining retrieval / agent nodes.
TERMINAL_PATH_KINDS: frozenset[str] = frozenset({"refuse", "decline", "crashed"})

#: Written to ``path_kind`` / ``failure`` to clear them for a new turn. See
#: :func:`settle_path_kind` for why a sentinel and not ``None``.
RESET = "reset"


class RewriteResult(TypedDict):
    before: str
    after: str
    outcome: Literal["rewritten", "unchanged", "failed"]


class NegativeVerdict(TypedDict):
    outcome: Literal["hit", "clear", "disabled", "error_failed_open"]
    tau: float | None
    top_score: float | None
    matched_id: str | None


class FacetResult(TypedDict):
    """One facet branch's output. ``facet`` is a :class:`~.stages.Stage` value."""

    facet: str
    queries: list[str]
    hits: list[Any]
    channels: dict[str, str]


class SchemaCrossing(TypedDict):
    from_schema: str
    into_schema: str
    table_id: str
    reason: Literal["steiner_point"]


class RetrievalResult(TypedDict):
    by_type: dict[str, list[str]]
    selected: dict[str, Any]
    attributions: dict[str, list[Any]]
    pulled_in: dict[str, Literal["resolve", "connect"]]
    schema_ranking: list[tuple[str, float]]
    lexical_coverage: float


class NodeFailure(TypedDict):
    """Which node raised, and what.

    ``detail`` was written by ``api/graph_app.py``'s ``accept`` node and declared nowhere,
    which is a field reaching the record through a hole in the schema. It is optional
    because ``wrap.py`` has only the exception type to offer, and a fabricated sentence
    there would read as a diagnosis nobody made.
    """

    stage: str
    error_type: str
    detail: NotRequired[str]


class Delivery(TypedDict):
    context_block: str | None
    context_hash: str | None
    tool_delivered: dict[str, str]
    delivery_hash: str | None


class UsageRecord(TypedDict):
    """One model-call cost row. ``turn_index`` is required for multi-turn projection.

    **The token counts are ``int | Measured[int]``, and the union is the point.** They
    were ``NotRequired[int]``, so the only value a turn could record when the provider
    reported nothing was ``0`` — a measured zero that ``measure/price.py`` prices as free.
    An ``int`` is what a provider reported; a :class:`~governed_bi.register.quantity.Measured`
    in the unmeasured state is the turn saying it was not told, with the reason attached.
    Absent is the third legal shape and means the same as unmeasured for the two cache
    fields, whose absence ``price.py`` reads as nothing cached from the artifacts.
    """

    turn_index: int
    model: NotRequired[str]
    input_tokens: NotRequired[int | Measured[int]]
    output_tokens: NotRequired[int | Measured[int]]
    cache_read_tokens: NotRequired[int | Measured[int]]
    cache_write_tokens: NotRequired[int | Measured[int]]


class Answer(TypedDict):
    """One question in, one answer out — every terminal path including crashes."""

    outcome: str
    text: str | None
    failed_stage: str | None
    error_type: str | None
    refused_by: str | None
    record: dict[str, Any]


def merge_facets(
    left: dict[str, FacetResult],
    right: Any,
) -> dict[str, FacetResult]:
    """Replace by key — right wins per key. :data:`RESET` clears.

    Concurrent-safe within a super-step (five disjoint facet keys) and
    overwrite-per-turn across turns (turn 2 writes the same five keys) — *provided the
    fan-out runs*. A turn refused at ``guard`` never reaches it, which is why the sentinel
    is honoured here too: without it, that turn stamped the **previous** turn's
    ``facet_hits``, ``facet_channels`` and ``facet_degraded`` into its own record.
    """
    if right == RESET:
        return {}
    merged = dict(left)
    merged.update(right)
    return merged


def settle_path_kind(left: Any, right: Any) -> Any:
    """First terminal wins; ``None`` is a no-op; :data:`RESET` clears.

    **Both channels this reducer and** :func:`settle_failure` **guard were un-reduced, and
    that is a crash the graph cannot record.** Five facet nodes run in one super-step and
    ``wrap.py`` turns any exception into ``{"failure": ..., "path_kind": "crashed"}``, so two
    facets failing means two writes to one un-reduced channel — ``InvalidUpdateError: At key
    'failure': Can receive only one value per step``. It is raised by the **channel**, after
    the nodes have returned, where ``wrap_node`` is no longer on the stack. Nothing catches
    it, ``stamp`` never runs, and the turn produces no record at all: the one failure mode
    the whole ``wrap_node`` design exists to make impossible.

    **Why the sentinel.** A per-turn channel has to be clearable, because ``path_kind``
    outlives its turn under a checkpointer: a crashed turn 1 left ``"crashed"`` in the
    channel, so ``_after_guard`` sent **turn 2 straight to stamp** and that thread could
    never be served again. But if ``None`` did the clearing, every node that returns a
    ``None`` path_kind would silently erase a terminal set by an earlier node — which is not
    hypothetical: ``route_node`` wrote ``"path_kind": None`` unconditionally, erasing a facet
    crash and buying a full billed model call on a turn that had already failed.

    So the two are separated. ``None`` means "this node has nothing to say", which is what a
    node returning a partial update actually means, and :data:`RESET` — written only by
    :meth:`~governed_bi.serve.session.Session.turn` — means "a new turn starts here". One
    caller must remember the sentinel and it is tested; the alternative asks every node to
    remember not to mention a field.

    First-wins rather than last-wins for the same reason: within a turn the graph routes
    every terminal straight to ``stamp``, so a *second* different terminal is a bug, and the
    first one is the causal one.
    """
    if right == RESET:
        return None
    if right is None:
        return left
    if left is None or left == right:
        return right
    return left


def settle_failure(left: Any, right: Any) -> Any:
    """First failure wins, and a concurrently dropped one is named in ``detail``.

    Same three cases as :func:`settle_path_kind`. The difference is that two concurrent
    failures are *different* values, so one is genuinely lost — and ``failed_stage`` is a
    single field in the register, so the record cannot hold both. Losing it silently would be
    the reportable-state-treated-as-nothing shape, so the dropped stage is appended to
    ``detail``, which is free text that already reaches the record.
    """
    if right == RESET:
        return None
    if right is None:
        return left
    if left is None:
        return right
    if left == right:
        return left
    also = f"{right.get('stage')}/{right.get('error_type')}"
    detail = left.get("detail")
    return {**left, "detail": f"{detail}; also failed: {also}" if detail else f"also failed: {also}"}


class ServeState(TypedDict, total=False):
    question: str
    thread_id: str
    turn_index: int
    #: GovernancePolicy is passed via ``configurable["policy"]``, not state
    #: (checkpointer cannot msgpack the dataclass).
    identity: dict[str, Any]
    run_id: str
    turn_id: str
    question_id: str
    db_id: str
    attempt_id: str
    corpus_content_hash: str
    prompt_set_hash: str
    knobs_resolved: dict[str, Any]

    guard: GuardVerdict
    rewrite: RewriteResult | None
    negative: NegativeVerdict

    facets: Annotated[dict[str, FacetResult], merge_facets]

    schemas: list[str]
    retrieved: RetrievalResult
    crossings: list[SchemaCrossing]
    licensed: list[str]

    delivery: Delivery
    messages: Annotated[list, add_messages]
    usage: Annotated[list[UsageRecord], operator.add]
    clarifications: Annotated[list[dict[str, Any]], operator.add]
    clarification_requested: bool

    execution: ExecutionRecord
    failure: Annotated[NodeFailure | None, settle_failure]
    answer: Answer | None

    terminal_reason: str | None
    path_kind: Annotated[PathKind | None, settle_path_kind]
    generated_sql: str | None
    n_re_served: int

    # F1 test hooks and per-turn knobs.
    #
    #: Five more fields lived here until 2026-08-03 -- ``references``, ``join_edges``,
    #: ``schema_tags``, ``asset_types``, ``table_schemas`` -- labelled *optional*, read
    #: by ``resolve`` and ``connect``, and **written by nothing in the repository**. The
    #: word "optional" was the defect in one word: two functions that cannot work
    #: without their inputs were declared not to need them, so ``connect`` ran on an
    #: empty edge set on every turn ever served. They are not five per-turn hooks but
    #: one projection of the corpus, so they now live in
    #: :class:`~governed_bi.retrieve.structure.CorpusStructure` on ``configurable``
    #: (ADR 0005 §2.8.2), where the thing that builds them is the thing that has them.
    facet_route_hits: list[tuple[Any, Any, float]]
    retrieve_hooks: dict[str, Any]
    route_top_n: int
    max_steiner_points: int
    max_crossings: int
    lexical_coverage: float


#: What :meth:`~governed_bi.serve.session.Session.turn` writes to clear the previous turn.
#:
#: A channel outlives its turn under a checkpointer, so every one of these was readable by
#: the *next* turn until 2026-08-04. Two of them changed that turn's outcome: a stale
#: ``path_kind="crashed"`` routed it straight to ``stamp``, and a stale ``negative`` verdict
#: was stamped into its record by a turn that never ran the gate.
PER_TURN_RESET: dict[str, Any] = {
    "path_kind": RESET,
    "failure": RESET,
    "facets": RESET,
    "terminal_reason": None,
    "guard": None,
    "rewrite": None,
    "negative": None,
    "retrieved": None,
    "delivery": None,
    "execution": None,
    "answer": None,
    "generated_sql": None,
    "schemas": [],
    "crossings": [],
    "licensed": [],
    "clarification_requested": False,
}

#: Channels that accumulate **across** turns on purpose, each row carrying its own
#: ``turn_index`` or ``turn_id`` so a projection can filter. Clearing one would destroy the
#: conversation (``messages``) or the run's cost history (``usage``).
ACCUMULATING: frozenset[str] = frozenset({"messages", "usage", "clarifications"})

#: Written by ``turn()`` itself — the turn's identity and the run's claims about itself.
TURN_IDENTITY: frozenset[str] = frozenset({
    "question", "turn_index", "thread_id", "identity", "run_id", "turn_id", "question_id",
    "db_id", "attempt_id", "corpus_content_hash", "prompt_set_hash", "knobs_resolved",
    "n_re_served",
})

#: Per-turn knobs and F1 injection points. A caller sets these *over* ``turn()``'s output, so
#: ``turn()`` must not write them: a reset here would overwrite the hook it was handed.
TEST_HOOKS: frozenset[str] = frozenset({
    "facet_route_hits", "retrieve_hooks", "route_top_n", "max_steiner_points",
    "max_crossings", "lexical_coverage",
})
