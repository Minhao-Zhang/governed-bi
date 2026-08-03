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
    "merge_facets",
]


PathKind = Literal["refuse", "decline", "answered", "crashed"]

#: Path kinds that short-circuit remaining retrieval / agent nodes.
TERMINAL_PATH_KINDS: frozenset[str] = frozenset({"refuse", "decline", "crashed"})


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
    stage: str
    error_type: str


class Delivery(TypedDict):
    context_block: str | None
    context_hash: str | None
    tool_delivered: dict[str, str]
    delivery_hash: str | None


class UsageRecord(TypedDict):
    """One model-call cost row. ``turn_index`` is required for multi-turn projection."""

    turn_index: int
    model: NotRequired[str]
    input_tokens: NotRequired[int]
    output_tokens: NotRequired[int]
    cache_read_tokens: NotRequired[int]
    cache_write_tokens: NotRequired[int]


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
    right: dict[str, FacetResult],
) -> dict[str, FacetResult]:
    """Replace by key — right wins per key.

    Concurrent-safe within a super-step (five disjoint facet keys) and
    overwrite-per-turn across turns (turn 2 writes the same five keys).
    """
    merged = dict(left)
    merged.update(right)
    return merged


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
    failure: NodeFailure | None
    answer: Answer | None

    terminal_reason: str | None
    path_kind: PathKind | None
    generated_sql: str | None
    n_re_served: int

    # F1 test / wiring hooks (optional)
    facet_route_hits: list[tuple[Any, Any, float]]
    retrieve_hooks: dict[str, Any]
    references: dict[str, Any]
    join_edges: set[tuple[Any, Any]]
    schema_tags: dict[str, str]
    asset_types: dict[str, str]
    table_schemas: dict[str, str]
    route_top_n: int
    max_steiner_points: int
    max_crossings: int
    lexical_coverage: float
