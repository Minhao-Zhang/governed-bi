"""See ADR 0005."""

from __future__ import annotations

import hashlib
import json
import operator
from collections.abc import Mapping
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langgraph.graph.message import add_messages

from governed_bi.govern.guard import GuardResult
from governed_bi.govern.ledger import ExecutionRecord
from governed_bi.registry.quantity import Measured

__all__ = [
    "RewriteResult",
    "AbstentionResult",
    "ChannelResult",
    "SchemaCrossing",
    "RetrievalResult",
    "NodeFailure",
    "Delivery",
    "UsageRecord",
    "Answer",
    "TurnEntry",
    "ServeInput",
    "ServeState",
    "PathKind",
    "TERMINAL_PATH_KINDS",
    "RESET",
    "PER_TURN_RESET",
    "ACCUMULATING",
    "TURN_IDENTITY",
    "TEST_HOOKS",
    "COMPACT_RECORD_BUDGET",
    "COMPACT_MIN_VALUE_BYTES",
    "MAX_TURNS_RETAINED",
    "protected_record_keys",
    "compact_turn_record",
    "keep_turns",
    "cleared",
    "merge_delta",
    "merge_channels",
    "settle_path_kind",
    "settle_failure",
]


PathKind = Literal["refuse", "decline", "answered", "crashed"]

TERMINAL_PATH_KINDS: frozenset[str] = frozenset({"refuse", "decline", "crashed"})

#: ``None`` is a no-op on these reducers, so it cannot mean clear.
RESET = "reset"


def cleared(left: Any) -> Any:
    """LangGraph 1.2.10 seeds a stripped ``Union`` as ``MISSING``, so the first write skips the reducer."""
    return None if isinstance(left, str) and left == RESET else left


class RewriteResult(TypedDict):
    before: str
    after: str
    outcome: Literal["rewritten", "unchanged", "failed"]


class ChannelResult(TypedDict):
    channel: str
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
    #: Missing is not a counted zero: a cap can discard a gold table.
    budget_dropped: NotRequired[dict[str, int]]
    #: A count cannot separate 0.97 from 0.01.
    budget_best_dropped_score: NotRequired[dict[str, float]]


class AbstentionResult(TypedDict):
    """See ADR 0013."""

    policy: str
    outcome: Literal["answer", "withhold", "disabled"]
    reason: str | None
    #: Present on an answer too, so letting the turn through is a recorded fact.
    rules_evaluated: list[str]
    evidence: dict[str, Any]


class NodeFailure(TypedDict):
    stage: str
    error_type: str
    detail: NotRequired[str]


class Delivery(TypedDict):
    context_block: str | None
    context_hash: str | None
    tool_delivered: dict[str, str]
    delivery_hash: str | None
    #: Missing is not a counted zero.
    evicted: NotRequired[dict[str, Any]]


class UsageRecord(TypedDict):
    """Unmeasured tokens are not zero."""

    turn_index: int
    model: NotRequired[str]
    input_tokens: NotRequired[int | Measured[int]]
    output_tokens: NotRequired[int | Measured[int]]
    cache_read_tokens: NotRequired[int | Measured[int]]
    cache_write_tokens: NotRequired[int | Measured[int]]
    #: An agent loop is one row, so the repeated input share is otherwise a guess.
    model_calls: NotRequired[int]


class Answer(TypedDict):
    outcome: str
    text: str | None
    failed_stage: str | None
    error_type: str | None
    refused_by: str | None
    record: dict[str, Any]


class TurnEntry(TypedDict, total=False):
    """Identity sits beside ``record``, not inside it."""

    #: UTC, to the second.
    asked_at: str
    question: str | None
    answer_text: str | None
    outcome: str | None
    record: dict[str, Any]
    #: Same list as ``record["assumptions"]``, beside the fields the chat page already reads.
    assumptions: list[dict[str, Any]]

    #: Absent only on the newest row, so an empty drop list means trimmed nothing.
    compacted: dict[str, Any]
    #: On the oldest survivor, where the gap is.
    elided_turns: int


#: Not a guarantee. Protected keys floor a real record near 6 KB; 8192 keeps the
#: two bulk keys on the measured 103.8 KB records.
COMPACT_RECORD_BUDGET = 8192

#: Trimming the 66-byte hash grew the row.
COMPACT_MIN_VALUE_BYTES = 256

#: Each super-step rewrites the channel (18 per turn measured). 25 plateaus near
#: 253 KB. Elided rows remain in that turn's checkpoints.
MAX_TURNS_RETAINED = 25

#: Register-optional keys a one-line summary of a past turn still needs.
_LIST_VIEW_KEYS: frozenset[str] = frozenset({
    "terminal_reason", "schemas", "generated_sql", "latency_sec", "retrieved_tables",
})


def protected_record_keys() -> frozenset[str]:
    """See ADR 0004. The wrapper names the trim, so a later re-declaration is not an omission."""
    from governed_bi.registry.record import required_keys

    return frozenset(required_keys()) | _LIST_VIEW_KEYS


def _sized(value: Any) -> int:
    """JSON length is a relative ruler; the checkpoint is msgpack."""
    try:
        return len(json.dumps(value, default=str))
    except Exception:  # noqa: BLE001 — unserialisable values are measured by repr
        return len(repr(value))


def _marker(name: str, value: Any) -> dict[str, Any]:
    """The key stays, so a trim is not an omission."""
    payload = json.dumps(value, default=str, sort_keys=True)
    return {
        "compacted": name,
        "bytes": len(payload),
        "n": len(value) if isinstance(value, (Mapping, list, tuple)) else None,
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16],
    }


def compact_turn_record(record: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """See ADR 0005. Byte-driven, not a key list, so a new bulk field is caught."""
    kept = dict(record)
    protected = protected_record_keys()
    total = _sized(kept)
    candidates = sorted(
        ((_sized(v), k) for k, v in kept.items() if k not in protected),
        key=lambda pair: (-pair[0], pair[1]),
    )
    dropped: list[str] = []
    for size, name in candidates:
        if total <= COMPACT_RECORD_BUDGET or size < COMPACT_MIN_VALUE_BYTES:
            break
        marker = _marker(name, kept[name])
        kept[name] = marker
        total -= size - _sized(marker)
        dropped.append(name)
    return kept, dropped


def _turn_id_of(row: Mapping[str, Any]) -> str | None:
    record = row.get("record")
    if not isinstance(record, Mapping):
        return None
    turn_id = record.get("turn_id")
    return str(turn_id) if turn_id else None


def keep_turns(left: Any, right: Any) -> list[dict[str, Any]]:
    """``operator.add`` cannot collapse a resumed turn."""
    rows: list[dict[str, Any]] = [
        dict(row) for row in (cleared(left) or ()) if isinstance(row, Mapping)
    ]
    incoming = right if isinstance(right, (list, tuple)) else ()
    for row in incoming:
        if not isinstance(row, Mapping):
            continue
        fresh = dict(row)
        turn_id = _turn_id_of(fresh)
        at = (
            next((i for i, held in enumerate(rows) if _turn_id_of(held) == turn_id), None)
            if turn_id
            else None
        )
        if at is None:
            rows.append(fresh)
        else:
            rows[at] = fresh

    # The newest row stays verbatim; ``answer`` already holds that turn's record.
    for index, row in enumerate(rows[:-1]):
        if "compacted" in row:
            continue
        record = row.get("record")
        if not isinstance(record, Mapping):
            continue
        was = _sized(record)
        trimmed, dropped = compact_turn_record(record)
        rows[index] = {**row, "record": trimmed, "compacted": {"dropped": dropped, "was_bytes": was}}

    if len(rows) > MAX_TURNS_RETAINED:
        gone = rows[: len(rows) - MAX_TURNS_RETAINED]
        rows = rows[len(rows) - MAX_TURNS_RETAINED :]
        elided = len(gone) + sum(int(row.get("elided_turns") or 0) for row in gone)
        rows[0] = {**rows[0], "elided_turns": int(rows[0].get("elided_turns") or 0) + elided}
    return rows


def merge_delta(left: Any, right: Any) -> Any:
    """One level only. A deeper merge would restore a collection a node narrowed.

    A prior turn's ``evicted`` must not merge forward.
    """
    if right is None or (isinstance(right, str) and right == RESET):
        return None
    base = cleared(left)
    if not isinstance(base, Mapping):
        return dict(right)
    return {**base, **right}


def merge_channels(
    left: dict[str, ChannelResult],
    right: Any,
) -> dict[str, ChannelResult]:
    """``cleared`` is defensive: this annotation strips to ``dict``, so ``left`` is never the sentinel."""
    if right == RESET:
        return {}
    merged = dict(cleared(left) or {})
    merged.update(right)
    return merged


def settle_path_kind(left: Any, right: Any) -> Any:
    """Concurrent crashes need a reducer, and ``None`` must not erase a prior terminal."""
    left = cleared(left)
    if right == RESET:
        return None
    if right is None:
        return left
    if left is None or left == right:
        return right
    return left


def settle_failure(left: Any, right: Any) -> Any:
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
    """See ADR 0005. Only the ``accept`` graph uses this schema."""

    #: ``accept`` derives the turn from the last human message and no other key.
    messages: Annotated[list, add_messages]


class ServeOutput(TypedDict, total=False):
    """See ADR 0005. ``output_schema`` narrows ``invoke`` only; streams and checkpoints are wider."""

    messages: Annotated[list, add_messages]
    answer: dict[str, Any]


class ServeState(TypedDict, total=False):
    question: str
    #: Empty on production paths. Per-turn, not config.
    evidence: str
    thread_id: str
    turn_index: int
    run_id: str
    turn_id: str
    question_id: str
    db_id: str
    attempt_id: str
    corpus_content_hash: str
    prompt_set_hash: str
    settings_resolved: dict[str, Any]

    guard: GuardResult
    rewrite: RewriteResult | None
    abstention: AbstentionResult | None

    channels: Annotated[dict[str, ChannelResult], merge_channels]

    schemas: list[str]
    #: Absent on served turns. The rewriters are model calls, so a moving shortlist
    #: cannot attribute an A/B delta.
    pinned_schemas: list[str] | None
    retrieved: Annotated[RetrievalResult, merge_delta]
    crossings: list[SchemaCrossing]
    #: A union would re-license a refused table.
    retrieved_tables: list[str]

    delivery: Annotated[Delivery, merge_delta]
    messages: Annotated[list, add_messages]
    usage: Annotated[list[UsageRecord], operator.add]
    clarifications: Annotated[list[dict[str, Any]], operator.add]
    clarification_requested: bool
    #: Not ``operator.add``: ``answer`` is cleared each turn, so this channel is what survives.
    turns: Annotated[list[TurnEntry], keep_turns]

    execution: ExecutionRecord
    failure: Annotated[NodeFailure | None, settle_failure]
    answer: Answer | None

    terminal_reason: str | None
    path_kind: Annotated[PathKind | None, settle_path_kind]
    generated_sql: str | None
    result_table: dict[str, Any] | None
    #: From ``narrate``, distinct from system ``answer["text"]``.
    answer_text: str | None
    #: The answer tool's list, after the server's checks. Empty when the tool was not accepted.
    assumptions: list[dict[str, Any]] | None
    #: True when an accepted ``answer`` call ended the turn. Finish records the fault when this stays unset.
    answer_tool_called: bool | None
    #: Accepted assumptions whose basis is ``guess``. Zero when no answer was accepted.
    guess_count: int | None
    #: Answer calls refused for a phrase two retrieved terms bind differently.
    ambiguous_guess_refusals: int | None
    #: Per-turn: streamed config is bound with no question.
    query_vector: list[float] | None
    #: Wall clock, so a resumed clarification still has a span when the checkpointer
    #: is durable. ``/chat`` keeps the saver in memory, so a process bounce cannot resume.
    turn_started_at: float | None
    n_re_served: int

    channel_route_hits: list[tuple[Any, Any, float]]
    retrieve_hooks: dict[str, Any]
    route_top_n: int
    max_steiner_points: int
    max_crossings: int
    lexical_coverage: float


PER_TURN_RESET: dict[str, Any] = {
    "path_kind": RESET,
    "failure": RESET,
    "channels": RESET,
    "terminal_reason": None,
    "guard": None,
    "rewrite": None,
    "abstention": None,
    "retrieved": None,
    "delivery": None,
    "execution": None,
    "answer": None,
    "generated_sql": None,
    "result_table": None,
    "answer_text": None,
    "assumptions": None,
    "answer_tool_called": None,
    "guess_count": 0,
    "ambiguous_guess_refusals": 0,
    "query_vector": None,
    # Or the next turn's latency includes the gap between turns.
    "turn_started_at": None,
    "schemas": [],
    # Eval writes this onto the returned dict, so the reset cannot erase a pin
    # just applied. Leaving it would route the next turn on this shortlist.
    "pinned_schemas": None,
    "crossings": [],
    "retrieved_tables": [],
    "clarification_requested": False,
}

#: Each row carries its own turn identity.
ACCUMULATING: frozenset[str] = frozenset(
    {"messages", "usage", "clarifications", "turns"}
)

TURN_IDENTITY: frozenset[str] = frozenset({
    "question", "evidence", "turn_index", "thread_id", "run_id", "turn_id",
    "question_id", "db_id", "attempt_id", "corpus_content_hash", "prompt_set_hash",
    "settings_resolved", "n_re_served",
})

#: Not in :data:`PER_TURN_RESET`, so a caller can set them on the turn dict.
TEST_HOOKS: frozenset[str] = frozenset({
    "channel_route_hits", "retrieve_hooks", "route_top_n", "max_steiner_points",
    "max_crossings", "lexical_coverage",
})
