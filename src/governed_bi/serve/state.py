"""Serve graph state and reducers (ADR 0005 §3.2).

``usage`` uses ``operator.add`` and therefore accumulates across turns under a
checkpointer. Every :class:`UsageRecord` must carry ``turn_index``; ``stamp``
filters to the current turn when projecting the register. Do not treat the raw
channel as the per-turn cost list.
"""

from __future__ import annotations

import operator
from collections.abc import Mapping
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langgraph.graph.message import add_messages

from governed_bi.govern.guard import GuardVerdict
from governed_bi.govern.ledger import ExecutionRecord
from governed_bi.register.quantity import Measured

__all__ = [
    "RewriteResult",
    "NegativeVerdict",
    "AbstentionVerdict",
    "FacetResult",
    "SchemaCrossing",
    "RetrievalResult",
    "NodeFailure",
    "Delivery",
    "UsageRecord",
    "Answer",
    "ServeInput",
    "ServeState",
    "PathKind",
    "TERMINAL_PATH_KINDS",
    "RESET",
    "PER_TURN_RESET",
    "ACCUMULATING",
    "TURN_IDENTITY",
    "TEST_HOOKS",
    "cleared",
    "merge_delta",
    "merge_facets",
    "settle_path_kind",
    "settle_failure",
]


PathKind = Literal["refuse", "decline", "answered", "crashed"]

#: Path kinds that short-circuit remaining retrieval / agent nodes.
TERMINAL_PATH_KINDS: frozenset[str] = frozenset({"refuse", "decline", "crashed"})

#: Written to ``path_kind`` / ``failure`` / ``facets`` to clear them for a new turn. See
#: :func:`settle_path_kind` for why a sentinel and not ``None``, and :func:`cleared` for the
#: LangGraph behaviour every reducer here has to survive.
RESET = "reset"


def cleared(left: Any) -> Any:
    """``None`` if ``left`` is the reset sentinel, else ``left``.

    Needed on ``path_kind`` and ``failure`` only: their annotations strip to a ``Union``, so
    ``BinaryOperatorAggregate``'s ``typ()`` seed raises, the channel starts ``MISSING``, and
    LangGraph assigns the first write raw, bypassing the reducer (1.2.10,
    ``channels/binop.py``). Fields typed ``dict``/``list``/``str`` seed empty and never see it,
    and ``LastValue`` has no reducer at all. It bites only on turn one of a fresh thread.
    """
    return None if isinstance(left, str) and left == RESET else left


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
    """One facet branch's output. ``facet`` is a
    :class:`~governed_bi.register.stages.Stage` value."""

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
    #: What a per-type cap discarded, ``{asset_type -> count}``, present only when one bit.
    #: Absent means the caps did not fire, which is a different fact from "nothing was
    #: dropped and we counted". ``register/citations.py`` states the requirement: a cap can
    #: discard a gold table, and without this the miss reads as retrieval never having found
    #: it. Declared here rather than smuggled onto the dict by ``pass_two``, which is how it
    #: came to be destroyed by ``resolve`` one super-step later on every turn that hit a cap.
    budget_dropped: NotRequired[dict[str, int]]
    #: Best score that did not survive, per type. A drop at 0.97 and a drop at 0.01 want
    #: opposite decisions and a bare count cannot tell them apart.
    budget_best_dropped_score: NotRequired[dict[str, float]]


class AbstentionVerdict(TypedDict):
    """What the declared abstention policy decided, and the evidence behind it (ADR 0013).

    Written on **every** turn that reaches the node, including the turns where the policy is
    off — ``negative``'s argument, one gate over: a gate that leaves a trace only when it fires
    cannot afterwards be told from one that was never wired up. ``outcome: "disabled"`` is the
    knob-off value and it carries no evidence, because gathering evidence for a decision nobody
    took would be a cost with no reader.

    There is no score here, and that is a decision rather than an omission. A graded
    ``confidence`` was measured and failed (open-work.md §3.11: the reflector's "unsure" bucket
    is as likely to be right as its "correct" one), and ADR 0007 forbids a trust field on the
    answer card. Reporting *why the engine withheld* is the ledger; scoring *how sure it is* is
    theatre.
    """

    #: The policy that ran, by name and version. Two runs under two policies are two treatments.
    policy: str
    outcome: Literal["answer", "withhold", "disabled"]
    #: A member of :data:`~governed_bi.register.stages.ABSTENTION_REASONS`, or ``None``.
    reason: str | None
    #: Every rule the policy asked, in the order it asked them. Present on an ``answer`` too, so
    #: "the policy considered this turn and let it through" is a recorded fact.
    rules_evaluated: list[str]
    #: Facts a person can check against the record without re-running the turn.
    evidence: dict[str, Any]


class NodeFailure(TypedDict):
    """Which node raised, and what. ``detail`` is optional free text."""

    stage: str
    error_type: str
    detail: NotRequired[str]


class Delivery(TypedDict):
    context_block: str | None
    context_hash: str | None
    tool_delivered: dict[str, str]
    delivery_hash: str | None
    #: What the char budget dropped, present only when it bit: ``bodies_dropped``,
    #: ``tables_dropped``, ``dropped_ids``, ``over_budget``. Absent means the block fit, which
    #: is a different fact from "nothing was dropped and we checked".
    evicted: NotRequired[dict[str, Any]]


class UsageRecord(TypedDict):
    """One model-call cost row. Token fields are ``int | Measured[int]`` (unmeasured ≠ zero)."""

    turn_index: int
    model: NotRequired[str]
    input_tokens: NotRequired[int | Measured[int]]
    output_tokens: NotRequired[int | Measured[int]]
    cache_read_tokens: NotRequired[int | Measured[int]]
    cache_write_tokens: NotRequired[int | Measured[int]]
    #: Model round trips this row paid for. An agent loop aggregates into one row, so without
    #: it the repeated share of the input -- the only part caching can remove -- is a guess.
    model_calls: NotRequired[int]


class Answer(TypedDict):
    """One question in, one answer out — every terminal path including crashes."""

    outcome: str
    text: str | None
    failed_stage: str | None
    error_type: str | None
    refused_by: str | None
    record: dict[str, Any]


def merge_delta(left: Any, right: Any) -> Any:
    """Merge a mapping channel by top-level key — right wins per key. ``None`` clears.

    The rule that lets a downstream node write **what it changed** instead of rebuilding the
    whole record from a key list it maintains itself. Both channels that use it were losing
    fields to exactly that:

    * ``retrieved`` — ``pass_two`` writes ``budget_dropped`` / ``budget_best_dropped_score``
      when a per-type cap discards a hit, and ``resolve``'s rebuild dropped both one
      super-step later, on every turn that hit a cap. Verified 2026-08-11: neither key had a
      reader anywhere in ``src/``, because neither key survived to a reader.
    * ``delivery`` — ``DeliveryTracker.merge_into`` rebuilt a four-key dict and destroyed
      ``assemble``'s ``evicted`` the same way. That one was fixed by hand, per channel, by
      carrying one named key. This is the same fix stated once, for any key.

    Right wins *per top-level key*, so a node that narrows a sub-collection — ``connect``
    dropping the assets of an unconnectable component — still replaces that key wholesale.
    The merge is one level deep on purpose: two levels would make a narrowing write additive
    and re-admit what the node just refused.

    ``None`` clears, because that is what :data:`PER_TURN_RESET` writes for both channels.
    Clearing matters more here than for an unreduced channel: without it turn one's
    ``evicted`` would merge into turn two's delivery and report an eviction that never
    happened. :func:`cleared` is applied to ``left`` for the same belt-over-braces reason
    :func:`merge_facets` gives.
    """
    if right is None or (isinstance(right, str) and right == RESET):
        return None
    base = cleared(left)
    if not isinstance(base, Mapping):
        return dict(right)
    return {**base, **right}


def merge_facets(
    left: dict[str, FacetResult],
    right: Any,
) -> dict[str, FacetResult]:
    """Replace by key — right wins. :data:`RESET` clears.

    The ``cleared()`` below is belt over braces: this annotation strips to ``dict``, so the
    channel seeds ``{}`` and this reducer runs from the first write — ``left`` is never the
    sentinel. See :func:`cleared` for where the call really is load-bearing.
    """
    if right == RESET:
        return {}
    merged = dict(cleared(left) or {})
    merged.update(right)
    return merged


def settle_path_kind(left: Any, right: Any) -> Any:
    """First terminal wins; ``None`` is a no-op; :data:`RESET` clears.

    Concurrent facet crashes need a reducer (un-reduced → InvalidUpdateError).
    ``None`` ≠ clear: nodes may return a null path_kind without erasing a prior terminal.
    """
    left = cleared(left)
    if right == RESET:
        return None
    if right is None:
        return left
    if left is None or left == right:
        return right
    return left


def settle_failure(left: Any, right: Any) -> Any:
    """First failure wins; a concurrent second is named in ``detail``."""
    left = cleared(left)
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


class ServeInput(TypedDict, total=False):
    """Everything a client is allowed to write into the graph. Deliberately one key.

    The write half of the trust boundary — audit-2026-08-10 §A2/§A3, which measured a client
    forging ``licensed``, ``corpus_content_hash`` and ``identity`` straight into ``ServeState``.

    ``trust()`` forces run constants over a caller's ``configurable``, but the graph's own
    ``input`` is a second write channel: ``langgraph_api`` forwards the client's dict unfiltered,
    ``PER_TURN_RESET`` does not clear :data:`TEST_HOOKS`, and ``int_knob`` reads state *before*
    ``knobs_resolved`` — so a request could set ``route_top_n`` while the record published the
    default. ``input_schema`` drops undeclared keys at the entry; measured on langgraph 1.2.10,
    ``route_top_n=99`` reaches the first node as absent, not as 99.

    Only the ``accept`` variant gets this. ``build_graph()`` without ``accept`` is entered by
    ``serve/__main__``, ``eval/`` and ``/chat``, which build the turn in-process through
    ``Session.turn()`` and legitimately pass the whole of :class:`ServeState`.
    """

    #: The conversation. ``serve/accept.py`` derives the whole turn from its last human message;
    #: it reads no other state key, which is what makes one key sufficient.
    messages: Annotated[list, add_messages]


class ServeOutput(TypedDict, total=False):
    """What ``invoke`` hands back — **the ``invoke`` half only** of the read boundary.

    Two keys, matching what the interface consumes: the transcript the SDK reconciles, and the
    turn's whole result. Adding a key here is the deliberate act.

    **``output_schema`` narrows ``invoke`` and nothing else**, which is audit-2026-08-10 §B1 and
    is still open. Re-measured on langgraph 1.2.11: the compiled ``accept`` graph's
    ``stream_channels_asis`` is all **46** declared channels, so a ``values`` frame and
    ``get_state(...).values`` both still return ``identity`` (the token
    :func:`~governed_bi.serve.resume.resume_authorised` gates clarification resume on) and
    ``delivery`` (the whole rendered corpus context block). This class is not a guarantee about
    the streamed or checkpoint-read surfaces.
    """

    messages: Annotated[list, add_messages]
    answer: dict[str, Any]


class ServeState(TypedDict, total=False):
    question: str
    #: Caller-supplied hint (empty on production paths). Per-turn, not config.
    evidence: str
    thread_id: str
    turn_index: int
    #: GovernancePolicy rides ``configurable["policy"]`` (not msgpack-safe).
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
    #: The declared abstention policy's verdict (ADR 0013). Written by ``abstain``, read by
    #: ``graph._after_abstain`` for the routing and by ``stamp`` for the record.
    abstention: AbstentionVerdict | None

    facets: Annotated[dict[str, FacetResult], merge_facets]

    schemas: list[str]
    #: Eval only: a shortlist replayed from a prior artifact, honoured by ``route`` in place of
    #: its own ranking (``eval/replay.py``). Absent on every served turn. It exists because the
    #: five facet rewriters are model calls, so two runs of one question can hand ``route``
    #: different hits — and an A/B that lets the shortlist move cannot attribute its own delta.
    pinned_schemas: list[str] | None
    #: Reduced by :func:`merge_delta`: ``route`` writes the whole result, ``resolve`` and
    #: ``connect`` write only the keys they change.
    retrieved: Annotated[RetrievalResult, merge_delta]
    crossings: list[SchemaCrossing]
    #: **Not** reduced, deliberately. ``connect`` *narrows* this set when a component cannot
    #: be joined, and a merge rule that unioned writes would re-license a table the node had
    #: just refused — govern's table allowlist growing back by reducer.
    licensed: list[str]

    #: Reduced by :func:`merge_delta`: ``assemble`` writes the block, ``agent_core`` writes
    #: only the tool-delivery keys.
    delivery: Annotated[Delivery, merge_delta]
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
    #: Last successful query result ``{columns, rows, row_count, truncated}``. Live only (ADR 0006 §11).
    result_table: dict[str, Any] | None
    #: Prose answer from ``narrate``. Live only; distinct from system ``answer["text"]``.
    answer_text: str | None
    #: The post-hoc observer's judgement (``serve/nodes/reflect.py``). Nothing routes on it:
    #: no conditional edge reads it and ``stamp`` only copies it to the record.
    reflect_verdict: dict[str, Any] | None
    #: Question embedding. Per-turn (streamed path cannot put it on load-time config).
    query_vector: list[float] | None
    #: Epoch seconds when the turn's first node ran. ``wrap_node`` writes it, ``stamp`` derives
    #: ``latency_sec`` from it. Wall clock so a clarification resume after a process bounce
    #: still yields a defined span *if* a durable checkpointer is present. `/chat` today uses
    #: ``InMemorySaver`` only — resume across processes is not supported there
    #: (``hitl_survives_process_restart: false``).
    turn_started_at: float | None
    n_re_served: int

    # F1 test hooks and per-turn knobs.
    facet_route_hits: list[tuple[Any, Any, float]]
    retrieve_hooks: dict[str, Any]
    route_top_n: int
    max_steiner_points: int
    max_crossings: int
    lexical_coverage: float


#: Cleared by :meth:`~governed_bi.serve.session.Session.turn` so a prior turn cannot leak.
PER_TURN_RESET: dict[str, Any] = {
    "path_kind": RESET,
    "failure": RESET,
    "facets": RESET,
    "terminal_reason": None,
    "guard": None,
    "rewrite": None,
    "negative": None,
    "abstention": None,
    "retrieved": None,
    "delivery": None,
    "execution": None,
    "answer": None,
    "generated_sql": None,
    "result_table": None,
    "answer_text": None,
    "reflect_verdict": None,
    "query_vector": None,
    # Cleared per turn, or turn two's `latency_sec` spans everything the user did in between.
    "turn_started_at": None,
    "schemas": [],
    # Cleared like any other per-turn channel: the eval writes it onto the turn dict *after*
    # `Session.turn` returns, so resetting here cannot erase it, and not resetting would let
    # turn one's pinned shortlist silently route turn two.
    "pinned_schemas": None,
    "crossings": [],
    "licensed": [],
    "clarification_requested": False,
}

#: Channels that accumulate across turns (each row carries turn identity).
ACCUMULATING: frozenset[str] = frozenset({"messages", "usage", "clarifications"})

#: Written by ``turn()`` itself — turn identity and run claims.
TURN_IDENTITY: frozenset[str] = frozenset({
    "question", "evidence", "turn_index", "thread_id", "identity", "run_id", "turn_id",
    "question_id", "db_id", "attempt_id", "corpus_content_hash", "prompt_set_hash",
    "knobs_resolved", "n_re_served",
})

#: Per-turn knobs and F1 hooks. Caller sets these over ``turn()``'s output.
TEST_HOOKS: frozenset[str] = frozenset({
    "facet_route_hits", "retrieve_hooks", "route_top_n", "max_steiner_points",
    "max_crossings", "lexical_coverage",
})
