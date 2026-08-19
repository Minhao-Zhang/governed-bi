"""Serve graph state and reducers (ADR 0005 §3.2).

``usage`` uses ``operator.add`` and therefore accumulates across turns under a
checkpointer. Every :class:`UsageRecord` must carry ``turn_index``; ``stamp``
filters to the current turn when projecting the register. Do not treat the raw
channel as the per-turn cost list.
"""

from __future__ import annotations

import hashlib
import json
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


class TurnEntry(TypedDict, total=False):
    """One finished turn's audit envelope — the same five keys the JSONL log writes.

    These five keys were the JSONL log's line shape, kept when the log was deleted because the
    audit surface already read them and ``api/thread_turns.summarise_turn`` still projects them.
    ``api/graph_app.record_node`` builds one envelope; there is no second sink left to disagree
    with it.

    ``question`` / ``answer_text`` sit beside ``record`` rather than inside it for
    ``append_turn``'s reason: merged in, every record read back out fails ``undeclared_keys``.

    ``total=False`` and not ``NotRequired`` per key: the log's own entries are written by a
    function that always fills all five, so absence here means "an older row", not "optional
    field", and a reader must tolerate it either way.

    Three fields are ``| None`` because ``append_turn`` really writes ``None`` into them — a
    refusal has no prose, and a turn derived from a non-text message has no question. Declaring
    them ``str`` would describe a shape the production writer violates, and the readers
    (``api/thread_turns.summarise_turn``, the audit routes) already treat null as "not present".
    """

    #: UTC isoformat to the second, stamped when the turn was recorded.
    asked_at: str
    question: str | None
    answer_text: str | None
    outcome: str | None
    record: dict[str, Any]

    #: What :func:`compact_turn_record` replaced on this row, ``{"dropped": [...], "was_bytes": n}``.
    #: Present on **every** archived row and absent only on the newest, so ``{"dropped": []}`` is a
    #: statement ("archived, nothing trimmed") and not a missing field. Lives on the envelope and
    #: not inside ``record`` for the reason ``question`` does: a key the register does not declare
    #: makes every read of that record fail ``undeclared_keys``.
    compacted: dict[str, Any]
    #: Turns dropped from this thread's history *before* this row, because
    #: :data:`MAX_TURNS_RETAINED` bit. Carried on the **oldest surviving** row, which is where the
    #: gap is. ADR 0009 D2: a cap that says nothing reads as full coverage, and ``/audit/turns``
    #: has no truncation field on the wire — so this is the only place the elision is stated, and
    #: surfacing it is owed by whoever next changes that response shape.
    elided_turns: int


#: Bytes an **archived** turn's ``record`` is trimmed toward — a target, not a guarantee: the
#: protected keys of :func:`protected_record_keys` are never compacted and set a floor of ~6 KB
#: on a real record (``knobs_resolved`` alone is 3.0 KB of it).
#:
#: 8 192 is measured, not chosen: on the real 103.8 KB records in ``runs/conversations.sqlite``
#: (2026-08-18) exactly two keys are above the line — ``facet_hits`` at 49.8 KB and ``pulled_in``
#: at 46.2 KB, together 92% of the record — and trimming those two lands at 7.5 KB. So this value
#: compacts the two bulk diagnostics and nothing else. A lower budget would start replacing
#: sub-kilobyte fields for no measurable gain.
COMPACT_RECORD_BUDGET = 8192

#: Values below this are never compacted, because the marker would be **larger** than the value.
#: The 66-byte ``context_hash`` is the case that made this necessary: trimming it grew the row.
COMPACT_MIN_VALUE_BYTES = 256

#: Rows :attr:`ServeState.turns` keeps. **This is what makes growth bounded rather than merely
#: cheaper.** ``AsyncSqliteSaver`` has two tables and no per-channel blob store, so every
#: super-step re-serialises the whole channel: at the measured 18 super-steps per served turn, a
#: retained history of ``n`` rows costs ``18 * n * row_bytes`` of writes **for one turn**.
#:
#: Priced on the real records (2026-08-18, ``runs/conversations.sqlite``), 25 rows plateaus the
#: channel at 253 KB — 4.4 MB of writes per turn, of which 1.6 MB is the one verbatim newest row —
#: and it stays there for turn 26 and turn 400. ``operator.add`` reached 33.8 MB by turn 25 and 54
#: MB by turn 40, and did not stop. Over forty turns the two are 135 MB and 1 111 MB of writes.
#: Retention does not save the unbounded case: under ``langgraph dev`` the TTL sweep is
#: ``return (0, 0)`` ("Not implemented for inmem server"), so nothing evicts locally.
#:
#: 25 is a judgement and the arithmetic above is how to move it. It leaves ``turns`` roughly level
#: with — no longer dominating — the ~5 MB per turn that ``answer`` (88 KB), ``delivery`` (80 KB)
#: and ``retrieved`` (75 KB) cost on the same thread whatever this channel does. Those three are
#: per-turn, so they are a constant this cannot touch; a cheaper conversation store has to start
#: there next.
#:
#: Elided rows are not destroyed: their full ``answer["record"]`` is still in that turn's own
#: checkpoints, and ``AsyncSqliteSaver`` prunes nothing (verified — three turns, three distinct
#: ``answer`` records reachable through ``alist`` after ``PER_TURN_RESET`` had run twice). Nothing
#: reads history today, which is why the elision is also *stated* on the row above the gap.
MAX_TURNS_RETAINED = 25

#: Record keys the audit list view projects that the register does **not** require. Mirrors
#: ``api/thread_turns.SUMMARY_FIELDS`` and ``summarise_turn``'s ``licensed_count``, spelled here
#: rather than imported because ``serve`` sits below ``api`` in the layering;
#: ``tests/serve/test_a_thread_keeps_every_turn.py`` asserts the two agree, so the copy cannot
#: drift silently.
_LIST_VIEW_KEYS: frozenset[str] = frozenset({
    "terminal_reason", "schemas", "generated_sql", "latency_sec", "licensed",
})


def protected_record_keys() -> frozenset[str]:
    """Record keys :func:`compact_turn_record` must never touch.

    Derived from :data:`~governed_bi.register.record.RECORD_REGISTER` rather than listed, which is
    what keeps the **read-time register judgement** ADR 0004 §2 defends: ``missing_required``
    reads exactly the ``Absence.never`` fields, so retaining all of them verbatim means
    ``incomplete_fields`` is still computed at read time, against today's declaration, and still
    gets the same answer it would have got from the untouched record. Compaction therefore buys
    ~14× on storage *without* moving that judgement to write time.

    What it does **not** buy: a field that is ``not_applicable`` today and is re-declared
    ``never`` tomorrow may have been compacted on old rows, and would then read as absent. That
    is why the trim is named on the envelope (:attr:`TurnEntry.compacted`) instead of being done
    silently — the alternative reading, "the stage never produced it", is a different and false
    fact about the turn.
    """
    from governed_bi.register.record import required_keys

    return frozenset(required_keys()) | _LIST_VIEW_KEYS


def _sized(value: Any) -> int:
    """Bytes ``value`` costs as JSON. Only a *relative* measure is needed — the checkpoint is
    msgpack — so the cheapest stable ruler is the right one."""
    try:
        return len(json.dumps(value, default=str))
    except Exception:  # noqa: BLE001 — an unserialisable value is compacted on its repr instead
        return len(repr(value))


def _marker(name: str, value: Any) -> dict[str, Any]:
    """What an archived record carries in place of a bulk value.

    **The key stays present.** Deleting it would make ``/audit/turns/{id}/trace`` render
    ``present: false`` beside the register's "why", which says the stage did not produce the
    value — a truncation reading as an omission, the shape ADR 0009 D2 refuses. A value that
    names itself cannot be misread, and ``sha256`` lets a reader match it against the full copy in
    that turn's own checkpoint.
    """
    payload = json.dumps(value, default=str, sort_keys=True)
    return {
        "compacted": name,
        "bytes": len(payload),
        "n": len(value) if isinstance(value, (Mapping, list, tuple)) else None,
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16],
    }


def compact_turn_record(record: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Trim an archived turn's record toward :data:`COMPACT_RECORD_BUDGET`.

    Returns the new record and the names trimmed, largest value first. Byte-driven rather than
    key-listed, so a future bulk field is caught by the rule instead of needing to be remembered
    (ADR 0005 §6, "no hand-maintained field lists") — and a field that is small stays verbatim
    however large the register grows.
    """
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
    """Append finished turns to a **bounded, deduplicated** history. Replaces ``operator.add``.

    ``operator.add`` had two defects, and only one of them was visible:

    * **Quadratic checkpoint growth.** ``AsyncSqliteSaver`` has exactly two tables and no
      per-channel blob store, so every super-step serialises the entire checkpoint into one BLOB.
      Measured on the real store: 18 super-steps per served turn, 103.8 KB per record, so turn
      *n* rewrote the previous *n-1* records eighteen times — 5.89 MB of writes for a two-turn
      thread, ~30 MB for the last turn of a twenty-turn one. Retention does not save us: under
      ``langgraph dev`` the TTL sweep is ``return (0, 0)`` ("Not implemented for inmem server").
      :func:`compact_turn_record` shrinks the row ~14× and :data:`MAX_TURNS_RETAINED` bounds how
      many there are; together they replace an unbounded term with a fixed one.
    * **Duplicate rows.** ``operator.add`` cannot deduplicate, so a turn resumed after an
      ``ask_user`` interrupt could append its envelope twice and the audit list would show one
      question answered twice. ``record["turn_id"]`` is the upsert key the register already
      declares for exactly this, so a repeat write **replaces** rather than appends.

    Oldest first, as before — ``api/thread_turns`` reverses per thread and sorts on ``asked_at``.
    """
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

    # Only the newest row keeps its record verbatim: it is the one an operator debugging the turn
    # they just ran opens, and `answer` already holds the same record for that turn anyway.
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

    **``output_schema`` narrows ``invoke``**, and audit-2026-08-10 §B1 is about what it does *not*
    narrow. Two measurements on langgraph 1.2.11, of two different things, and the difference
    matters:

    - the compiled ``accept`` graph's ``stream_channels_asis`` is all **47** declared channels;
    - but the root ``values`` frames of an actual streamed run carried **only** ``answer`` and
      ``messages`` — 4 frames, 0 containing ``turns``, measured over the wire from the client.

    So the graph *attribute* is wide and the frames are not. ``get_state(...)`` and
    ``GET /threads/{id}/state`` **are** wide: both return ``identity`` (the token
    :func:`~governed_bi.serve.resume.authorise_resume` gates clarification resume on) and
    ``delivery`` (the whole rendered corpus context block). This class is not a guarantee about
    either surface.

    Count went 46 → 47 with :attr:`ServeState.turns`. That channel widens the §B1 remainder on the
    **checkpoint-read** surfaces and not on the streamed one: what escapes there is one
    ``answer["record"]`` per read today and every prior turn's record once ``turns`` is present.
    Since ``GET /threads/*`` requires no credential (finding A7), that is the surface to price.
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
    #: Every finished turn of this thread, appended by ``api/graph_app.record_node``. This is the
    #: channel that makes a checkpoint hold the **whole conversation's** audit trail rather than
    #: only the newest turn: ``answer``, ``execution`` and ``generated_sql`` are all in
    #: :data:`PER_TURN_RESET`, so turn two erases turn one's record and a now-deleted JSONL log
    #: was the only surviving copy — a reader with the thread id and no filesystem access could
    #: not say what the conversation had already been told. Hence ``operator.add`` and
    #: :data:`ACCUMULATING`, never ``PER_TURN_RESET``. Deleting that log is what this channel
    #: made possible.
    #:
    #: Each row carries turn identity inside ``record`` — ``turn_id``, ``run_id``, ``thread_id``,
    #: ``question_id``, and ``record_node`` refuses to append a row with no ``turn_id`` — which is
    #: what ``ACCUMULATING`` requires: the rows of every turn are one flat list, so a reader that
    #: cannot tell whose row it is holding would attribute turn one's refusal to turn three. Same
    #: defect the ``usage`` channel's ``turn_index`` exists to prevent, one level up. ``turn_id``
    #: is also what :func:`keep_turns` deduplicates on.
    #:
    #: Reduced by :func:`keep_turns` and **not** ``operator.add``, which grew this channel — and
    #: therefore every super-step's checkpoint BLOB — without bound. That function carries the
    #: measurement and the bound.
    turns: Annotated[list[TurnEntry], keep_turns]

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
ACCUMULATING: frozenset[str] = frozenset({"messages", "usage", "clarifications", "turns"})

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
