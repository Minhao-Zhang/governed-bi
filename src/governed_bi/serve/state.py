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

    Required because LangGraph assigns the first channel value without calling the reducer,
    so ``RESET`` written by ``Session.turn`` can land as a bare string.
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
    """Which node raised, and what. ``detail`` is optional free text."""

    stage: str
    error_type: str
    detail: NotRequired[str]


class Delivery(TypedDict):
    context_block: str | None
    context_hash: str | None
    tool_delivered: dict[str, str]
    delivery_hash: str | None


class UsageRecord(TypedDict):
    """One model-call cost row. Token fields are ``int | Measured[int]`` (unmeasured ≠ zero)."""

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
    """Replace by key — right wins. :data:`RESET` clears."""
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

    **The defect this closes (audit §4.3).** ``trust()`` forces run constants over a
    caller-supplied ``configurable``, and that is one of *two* channels a caller can write.
    The other is the graph's own ``input``: ``langgraph_api`` forwards the client's dict to
    the graph with no filtering, ``PER_TURN_RESET`` does not clear :data:`TEST_HOOKS`, and
    ``int_knob`` reads state *before* ``knobs_resolved``. So a request could set
    ``route_top_n`` and the record would publish the default it did not use.

    ``StateGraph(ServeState, input_schema=ServeInput)`` drops undeclared keys at the entry
    rather than policing them, which is the difference between a rule and an impossibility.
    Measured on langgraph 1.2.10: a caller passing ``route_top_n=99`` alongside its messages
    reaches the first node as absent, not as 99.

    **Only the ``accept`` variant gets this.** ``build_graph()`` with no ``accept`` is entered
    by ``serve/__main__``, ``eval/`` and ``/chat``, which construct the turn in-process through
    ``Session.turn()`` and legitimately pass the whole of :class:`ServeState`. The untrusted
    entry is the one where a turn is *derived* from a client conversation, and that is exactly
    the one ``accept`` marks.
    """

    #: The conversation. ``_accept_node`` derives the whole turn from its last human message;
    #: it reads no other state key, which is what makes one key sufficient.
    messages: Annotated[list, add_messages]


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
    #: Last successful query result ``{columns, rows, row_count, truncated}``. Live only (ADR 0006 §11).
    result_table: dict[str, Any] | None
    #: Prose answer from ``narrate``. Live only; distinct from system ``answer["text"]``.
    answer_text: str | None
    #: The post-hoc observer's judgement (``serve/nodes/reflect.py``). Nothing routes on it, by
    #: construction: no conditional edge reads it and ``stamp`` only copies it to the record.
    reflect_verdict: dict[str, Any] | None
    #: Question embedding. Per-turn (streamed path cannot put it on load-time config).
    query_vector: list[float] | None
    #: Epoch seconds when the turn's first node ran. ``wrap_node`` writes it, ``stamp`` reads it
    #: to derive ``latency_sec``. Wall clock rather than ``perf_counter`` because a clarification
    #: can resume in a different process.
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
    "retrieved": None,
    "delivery": None,
    "execution": None,
    "answer": None,
    "generated_sql": None,
    "result_table": None,
    "answer_text": None,
    "reflect_verdict": None,
    "query_vector": None,
    # The turn's clock. Cleared per turn, or turn two's `latency_sec` would span everything the
    # user did between the two questions. `wrap_node` writes it from the first node to run.
    "turn_started_at": None,
    "schemas": [],
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
