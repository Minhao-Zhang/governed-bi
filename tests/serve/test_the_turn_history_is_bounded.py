"""``ServeState.turns`` under :func:`~governed_bi.serve.state.keep_turns`: bounded, deduplicated,
and honest about what it trimmed.

**The defect.** ``turns`` was ``Annotated[list[TurnEntry], operator.add]`` and each row holds a
full turn record. ``AsyncSqliteSaver`` has exactly two tables — ``checkpoints`` and ``writes`` —
and no per-channel blob store, so every super-step serialises the *whole* checkpoint into one
BLOB. Measured on the real store (``runs/conversations.sqlite``, 2026-08-18): 18 super-steps per
served turn and 103.8 KB per record, so turn *n* rewrote the previous *n-1* records eighteen
times. A two-turn thread had already written 5.89 MB. End to end on production-sized records the
marginal cost of turn 10 was **14.55 MB**, rising by ~1.6 MB with every turn and never stopping —
the TTL sweep is ``return (0, 0)`` under ``langgraph dev``, so nothing evicts locally.

**Why these tests are mostly at the reducer and not through the graph.** The end-to-end fixture's
two-schema corpus produces a 7.5 KB record, which is under
:data:`~governed_bi.serve.state.COMPACT_RECORD_BUDGET` — so a turn driven through the real
topology never trims anything, and a compaction test written that way would assert nothing while
looking thorough. The record shape that matters is the production one, so it is built here with
the measured key sizes. The cap and the append order *are* asserted through the served graph,
because those depend on ``accept``-resets-then-``record``-appends.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from governed_bi.api.graph_app import record_node
from governed_bi.corpus.schema import ColumnAsset, SchemaAsset, TableAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.register.record import missing_required, required_keys, undeclared_keys
from governed_bi.serve.accept import accept_node
from governed_bi.serve.graph import as_sync, build_graph
from governed_bi.serve.scripted_model import ScriptedChatModel
from governed_bi.serve.session import from_assets
from governed_bi.serve.state import (
    COMPACT_MIN_VALUE_BYTES,
    MAX_TURNS_RETAINED,
    compact_turn_record,
    keep_turns,
    protected_record_keys,
)

# Duplicated from `test_a_thread_keeps_every_turn.py` rather than imported: `tests/` is not a
# package (see `test_register_closure._contracts`), so a sibling test module is not importable,
# and small fixtures are already duplicated across this directory rather than shared.


class _EchoConnector:
    dialect = "postgres"

    def execute(self, sql: str, max_rows: int | None = None) -> Any:
        return (["n"], [(1,)], False)


def _assets() -> list[Any]:
    return [
        SchemaAsset(id="sales", name="sales", summary="sales orders and customers"),
        TableAsset(
            id="sales.orders", schema="sales", physical_name="orders",
            summary="Orders placed by customers.", body="One row per order.",
            columns=("sales.orders.id",),
        ),
        ColumnAsset(
            id="sales.orders.id", schema="sales", parent_table="sales.orders",
            physical_name="id", summary="Primary key.",
        ),
    ]


@pytest.fixture(autouse=True)
def _isolated():
    """``trust()`` is process-wide; a leaked registration makes another test pass by accident."""
    from governed_bi.serve.runtime import trust

    trust()
    yield
    trust()


def _fat_record(turn_id: str = "turn-0001") -> dict[str, Any]:
    """A record shaped like the real ones, with the two bulk keys at their measured sizes.

    ``facet_hits`` 49.8 KB and ``pulled_in`` 46.2 KB — together 92% of a 103.8 KB record. Every
    field the register declares ``Absence.never`` is filled, so ``missing_required`` is empty
    before compaction and the test can attribute any change to compaction itself.
    """
    return {
        "run_id": "run-1", "turn_id": turn_id, "thread_id": "t-1", "question_id": "q-1",
        "db_id": "facilities", "attempt_id": "a-1", "corpus_content_hash": "c0ffee",
        "prompt_set_hash": "beef", "knobs_resolved": {f"knob_{i}": i for i in range(200)},
        "guard": {"allowed": True}, "execution": {"attempts": [{"passed": True}], "terminal": "ok"},
        "guardrail_errors": 0, "outcome": "answered",
        "usage": [{"turn_index": 1, "input_tokens": 10, "output_tokens": 2}],
        "n_re_served": 0,
        "terminal_reason": None, "schemas": ["facilities"],
        "generated_sql": "select count(*) from facilities.assets", "latency_sec": 12.5,
        "licensed": [f"facilities.t{i}" for i in range(20)],
        # The two that dominate.
        "facet_hits": {f"facet_{f}": [
            {"asset_id": f"facilities.t{i}.c{j}", "asset_type": "column",
             "queries": ["how many actively maintained assets"], "lexical": 0.4, "semantic": 0.2}
            for i in range(20) for j in range(5)
        ] for f in range(5)},
        "pulled_in": {f"facilities.t{i}.c{j}": "resolve" for i in range(40) for j in range(20)},
        # Small, and therefore must survive verbatim.
        "context_hash": "a" * 64,
        "facet_channels": {f"facet_{f}": {"lexical": "ran"} for f in range(5)},
    }


def _entry(record: dict[str, Any], question: str = "how many assets") -> dict[str, Any]:
    return {
        "asked_at": "2026-08-18T22:39:06+00:00", "question": question,
        "answer_text": "9,815", "outcome": record.get("outcome"), "record": record,
    }


def _bytes(value: Any) -> int:
    return len(json.dumps(value, default=str))


# ── the property the compaction had to keep ───────────────────────────────────


def test_an_archived_row_is_still_judged_by_todays_register_at_read_time() -> None:
    """**The trade this design refused to make**, asserted rather than described.

    ADR 0004 §2 defends ``incomplete_fields`` being computed at *read* time: a turn recorded
    before a register row existed is judged by the current declaration. The obvious way to shrink
    a row — store the list-view projection and nothing else — moves that judgement to write time.

    :func:`protected_record_keys` derives its keep-set from ``required_keys()``, so every field
    ``missing_required`` reads survives compaction byte-for-byte and the read-time answer is
    *identical*. That is what makes the 17× saving free of the trade-off, and it is the assertion
    that fails if someone lets the byte budget reach a required field.
    """
    full = _fat_record()
    assert missing_required(full) == frozenset(), "the fixture must start complete"

    archived, dropped = compact_turn_record(full)

    assert missing_required(archived) == missing_required(full), (
        f"compaction changed the read-time quotability verdict: {sorted(dropped)} were trimmed "
        "and one of them is a field `missing_required` reads. Required fields are protected by "
        "`protected_record_keys()`; the byte budget must never reach them."
    )
    for name in required_keys():
        assert archived[name] == full[name], f"{name} is required and was not kept verbatim"

    # And the wire columns the list view projects are untouched, so `/audit/turns` is unchanged.
    for name in ("turn_id", "run_id", "thread_id", "question_id", "db_id", "outcome",
                 "terminal_reason", "schemas", "generated_sql", "latency_sec", "execution"):
        assert archived[name] == full[name], name
    assert len(archived["licensed"]) == len(full["licensed"]), "licensed_count would move"


def test_the_keep_set_still_covers_every_column_the_audit_list_projects() -> None:
    """``serve`` sits below ``api``, so the list-view columns are *copied* into ``state.py``.

    A copy that nothing checks is a copy that drifts, and the drift is silent in the worst
    direction: a column ``SUMMARY_FIELDS`` gains and ``_LIST_VIEW_KEYS`` does not becomes a
    compactable field, and ``/audit/turns`` starts rendering a marker dict where the client's
    schema expects a string. Importing the reader here is fine — a test may read upward.
    """
    from governed_bi.api.thread_turns import SUMMARY_FIELDS

    protected = protected_record_keys()
    missing = set(SUMMARY_FIELDS) - protected
    assert not missing, (
        f"{sorted(missing)} are audit-list columns that compaction may replace with a marker. "
        "Add them to `state._LIST_VIEW_KEYS`."
    )
    # `summarise_turn` also reads `licensed`, which is not a SUMMARY_FIELDS entry.
    assert "licensed" in protected, "licensed_count is computed from it"


# ── what compaction does, and what it says it did ─────────────────────────────


def test_compaction_trims_the_two_bulk_keys_and_nothing_smaller() -> None:
    """Byte-driven, not key-listed: the rule finds the bulk without being told its names.

    ``COMPACT_RECORD_BUDGET`` is set where the measurement put it — above ``pulled_in`` and below
    everything else — so a *new* bulk field is caught by the same rule instead of needing to be
    remembered, and a small field is never touched however large the register grows.
    """
    full = _fat_record()
    before = _bytes(full)
    assert before > 90_000, f"the fixture is no longer production-sized: {before}"

    archived, dropped = compact_turn_record(full)

    assert dropped == ["facet_hits", "pulled_in"], dropped
    after = _bytes(archived)
    assert after < before / 10, f"{before} -> {after} is not the ~17x the design claims"
    # **The budget is a target, not a guarantee**, and the reason is a floor: every protected key
    # survives compaction, so a record whose protected keys alone exceed the budget stays over it.
    # Asserted as the floor rather than as `after > BUDGET` — this fixture's protected keys happen
    # to fit, so compaction *does* get under budget here, and demanding otherwise would fail a
    # correct implementation for being too good.
    survivors = {k: v for k, v in archived.items() if k in protected_record_keys()}
    assert set(survivors) >= set(protected_record_keys()) & set(full), (
        "compaction dropped a protected key; those are the audit list's columns and the floor the "
        "budget is measured against"
    )
    assert _bytes(survivors) <= after, "the protected keys are a subset of what survived"
    assert archived["context_hash"] == full["context_hash"], (
        "a 66-byte value was replaced by a ~90-byte marker, which grows the row it was meant to "
        f"shrink. COMPACT_MIN_VALUE_BYTES={COMPACT_MIN_VALUE_BYTES} is the guard."
    )


def test_a_trimmed_field_stays_present_and_says_so() -> None:
    """ADR 0009 D2: a silent cap reads as full coverage. Deleting the key would read as worse.

    ``/audit/turns/{id}/trace`` renders every register field with ``present`` and the register's
    own "why". A deleted ``facet_hits`` therefore renders ``present: false`` beside "counts alone
    cannot attribute a finding to an asset" — which asserts the *route* stage produced nothing, a
    false fact about the turn. So the key stays, holding a value that names itself.
    """
    archived, dropped = compact_turn_record(_fat_record())

    assert set(dropped) <= set(archived), "a trimmed key must not be deleted from the record"
    marker = archived["facet_hits"]
    assert marker["compacted"] == "facet_hits"
    assert marker["bytes"] > 40_000, "the marker must say how much it stood in for"
    assert marker["n"] == 5, "and how many entries there were"
    assert len(marker["sha256"]) == 16, "and be matchable against the full copy in history"

    # `/trace` also publishes `undeclared_keys`, so compaction must not invent a record key.
    assert undeclared_keys(archived) == undeclared_keys(_fat_record())


def test_the_trim_is_declared_on_the_envelope_not_inside_the_record() -> None:
    """Where the marker lives is forced, not chosen.

    ``TurnEntry``'s own docstring records why ``question`` sits beside ``record`` rather than in
    it: a key the register does not declare makes every read of that record fail
    ``undeclared_keys``. ``compacted`` is the same class of fact about the row, so it goes the
    same place — and ``{"dropped": []}`` on an archived row is a statement ("archived, nothing
    trimmed"), which is why it is written even when nothing was.
    """
    rows = keep_turns([], [_entry(_fat_record("t-1"))])
    assert "compacted" not in rows[0], "the newest row keeps its record verbatim"

    rows = keep_turns(rows, [_entry(_fat_record("t-2"))])
    assert rows[0]["compacted"] == {"dropped": ["facet_hits", "pulled_in"],
                                    "was_bytes": _bytes(_fat_record("t-1"))}
    assert "compacted" not in rows[1]
    assert "compacted" not in rows[0]["record"], "it must not enter the record"

    # A small record is still marked, so "archived and complete" is a readable state.
    small = keep_turns([_entry({"turn_id": "s-1", "outcome": "refused"})],
                       [_entry({"turn_id": "s-2", "outcome": "answered"})])
    assert small[0]["compacted"] == {"dropped": [], "was_bytes": small[0]["compacted"]["was_bytes"]}


def test_the_newest_row_keeps_its_record_whole() -> None:
    """The one row an operator opens is the turn they just ran, so it is not trimmed.

    Stated as a test because it is the *cost* half of the design: it is ~86 KB of the ~256 KB the
    capped channel carries, duplicating ``answer`` in the same checkpoint. Trimming it too would
    save ~1.6 MB of writes per turn and cost the trace surface ``facet_hits`` on every turn.
    """
    rows = keep_turns([], [_entry(_fat_record("t-1"))])
    for i in range(2, 5):
        rows = keep_turns(rows, [_entry(_fat_record(f"t-{i}"))])

    assert _bytes(rows[-1]["record"]) > 90_000, "the newest record was trimmed"
    assert all(_bytes(r["record"]) < 20_000 for r in rows[:-1]), (
        [(_bytes(r["record"])) for r in rows]
    )


# ── the bound, and the dedup ``operator.add`` could not do ────────────────────


def test_the_history_is_capped_and_the_gap_is_stated() -> None:
    """The bound itself, plus the count that keeps the cap from reading as full coverage.

    ``/audit/turns`` has no truncation field on the wire and this change does not add one — the
    wire shape is a contract ``npm run check:api`` holds. ``elided_turns`` on the oldest surviving
    row is therefore the only statement that the history is partial, and it is where the gap is.
    """
    rows: list[dict[str, Any]] = []
    for i in range(MAX_TURNS_RETAINED + 15):
        rows = keep_turns(rows, [_entry({"turn_id": f"t-{i:04d}", "outcome": "answered"})])

    assert len(rows) == MAX_TURNS_RETAINED, len(rows)
    assert rows[-1]["record"]["turn_id"] == f"t-{MAX_TURNS_RETAINED + 14:04d}", "newest kept"
    assert rows[0]["elided_turns"] == 15, (
        "the count of dropped turns is wrong, so a reader cannot tell a 25-turn conversation "
        f"from a 400-turn one: {rows[0].get('elided_turns')}"
    )
    # It accumulates rather than resetting each time the cap bites — the row carrying it is
    # itself elided eventually, and a count that restarts under-reports the gap.
    for i in range(10):
        rows = keep_turns(rows, [_entry({"turn_id": f"u-{i}", "outcome": "answered"})])
    assert rows[0]["elided_turns"] == 25, rows[0].get("elided_turns")


def test_a_resumed_turn_appends_once() -> None:
    """``operator.add`` could not deduplicate, and a clarification resume re-runs ``record``.

    The visible cost was an audit list showing one question answered twice, with two rows carrying
    one ``turn_id`` — and ``get_turn`` returning whichever it scanned first. ``turn_id`` is the
    register's declared upsert key ("a reused graph deriving it once wrote every question to the
    same id"), so the later write replaces.
    """
    first = _entry({"turn_id": "t-resumed", "outcome": "clarification"}, "why")
    again = _entry({"turn_id": "t-resumed", "outcome": "answered"}, "why")

    rows = keep_turns(keep_turns([], [first]), [again])

    assert len(rows) == 1, f"the resumed turn appended a second row: {rows}"
    assert rows[0]["record"]["outcome"] == "answered", "the later write must win"

    # A row with no turn_id cannot be deduplicated and must not silently collapse two turns.
    anon = keep_turns([], [_entry({}), _entry({})])
    assert len(anon) == 2, anon


def test_the_reducer_survives_the_shapes_langgraph_hands_it() -> None:
    """A reducer that raises does it after the nodes returned, where ``wrap_node`` cannot catch it.

    That is the failure ``settle_failure`` exists for one channel over, and it costs the turn its
    whole record. So the empty seed, a non-list write and a non-mapping row are all no-ops here.
    """
    assert keep_turns([], []) == []
    assert keep_turns(None, [_entry({"turn_id": "x"})])[0]["record"]["turn_id"] == "x"
    assert keep_turns([_entry({"turn_id": "x"})], None)[0]["record"]["turn_id"] == "x"
    assert keep_turns([], ["not a row", 7, None]) == []
    assert keep_turns(["not a row"], [_entry({"turn_id": "x"})]) == [_entry({"turn_id": "x"})]
    # A row whose record is not a mapping is kept as-is rather than crashing the archive pass.
    kept = keep_turns([{"record": "nonsense"}], [_entry({"turn_id": "x"})])
    assert len(kept) == 2 and kept[0]["record"] == "nonsense"


# ── through the served topology ───────────────────────────────────────────────


def _served() -> Any:
    from governed_bi.serve.runtime import trust

    session = from_assets(
        _assets(),
        connector=_EchoConnector(),
        policy=GovernancePolicy(guard_rules_enabled={}),
        db_id="sales",
        corpus_content_hash_="corpus-under-test",
        agent_model=ScriptedChatModel(
            responses=[AIMessage(content="there are some orders")] * 40),
    )
    assert not session.fatal_problems, [str(p) for p in session.fatal_problems]
    trust(dict(session.configurable()["configurable"]))
    return as_sync(
        build_graph(accept=accept_node(session), record=record_node())
        .compile(checkpointer=InMemorySaver())
    )


def test_the_cap_holds_on_the_graph_production_mounts() -> None:
    """The cap through ``accept``-resets-then-``record``-appends, which is the pair that can break.

    ``MAX_TURNS_RETAINED`` is lowered for the run rather than asking 26 questions of a scripted
    model: the subject is the reducer running under the real reset, not the number 25. Every row
    still carries its own identity, which is what ``ACCUMULATING`` requires of a flat list.
    """
    import governed_bi.serve.state as state_mod

    graph = _served()
    thread = "t-bounded"
    cap = 3
    original = state_mod.MAX_TURNS_RETAINED
    state_mod.MAX_TURNS_RETAINED = cap
    try:
        for i in range(cap + 2):
            graph.invoke(
                {"messages": [HumanMessage(content=f"how many orders {i}")]},
                {"configurable": {"thread_id": thread}},
            )
        rows = list(
            graph.get_state({"configurable": {"thread_id": thread}}).values.get("turns") or []
        )
    finally:
        state_mod.MAX_TURNS_RETAINED = original

    assert len(rows) == cap, f"the cap did not hold through the served graph: {len(rows)} rows"
    assert rows[0]["elided_turns"] == 2, rows[0].get("elided_turns")
    assert [r["question"] for r in rows] == [
        f"how many orders {i}" for i in range(2, cap + 2)
    ], "oldest-first order, newest kept"
    assert len({r["record"]["turn_id"] for r in rows}) == cap, "every row addressable"
    assert all(r["record"]["thread_id"] == thread for r in rows), "no cross-thread leak"
