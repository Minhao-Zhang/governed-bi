"""The audit surface, and the one property that makes it maintainable.

`POST /chat` returns the record inline, once, to the caller who asked. There was no way
to see a turn again — the governance ledger, the licensed set and the retrieval
attributions were produced, published to a single HTTP response, and dropped.

The tests that matter here are not "the route returns 200". They are:

1. the turn log round-trips a record without inventing or losing a field, and
2. the trace's stage grouping is **derived from ``RECORD_REGISTER``**, so a field added
   to the register appears in the trace with no edit to the route.

(2) is the one worth a structural test. A hand-written stage→fields map is exactly the
drift ``register/`` exists to end, and it would pass every behavioural test on the day
it was written.
"""

from __future__ import annotations

import json
from typing import Any

import pytest


@pytest.fixture
def turn_log(tmp_path, monkeypatch):
    """Point the store at a temp dir. Never the repository's own ``runs/serve``."""
    from governed_bi.api import trace_store

    monkeypatch.setattr(trace_store, "TURN_LOG_DIR", tmp_path / "serve")
    return trace_store


def _record(turn_id: str = "t-1", **extra: Any) -> dict[str, Any]:
    return {
        "turn_id": turn_id,
        "run_id": "r-1",
        "thread_id": "th-1",
        "question_id": "q-1",
        "db_id": "corpus-x",
        "outcome": "answered",
        "terminal_reason": None,
        "schemas": ["airline", "shakespeare"],
        "licensed": ["airline.Air_Carriers_66c534"],
        "generated_sql": 'SELECT COUNT(*) FROM airline."Air Carriers"',
        "execution": {
            "terminal": "answered",
            "attempts": [{"passed": True, "reason_code": "passed", "path": "agent"}],
        },
        **extra,
    }


def test_a_logged_turn_round_trips(turn_log) -> None:
    """What went in comes out, and the summary is a projection rather than a rewrite."""
    turn_id, error = turn_log.append_turn(
        _record(), question="how many air carriers are listed?", answer_text="1,656."
    )
    assert error is None, error
    assert turn_id == "t-1"

    turns = turn_log.list_turns()
    assert len(turns) == 1
    row = turns[0]
    assert row["question"] == "how many air carriers are listed?"
    assert row["outcome"] == "answered"
    assert row["licensed_count"] == 1
    assert row["attempts"] == 1 and row["attempts_passed"] == 1

    # Every summary column is a *record* field name, so the list and the detail view
    # cannot disagree about what a column means.
    from governed_bi.register.record import record_keys

    declared = record_keys()
    for name in turn_log.SUMMARY_FIELDS:
        assert name in declared, (
            f"{name!r} is a summary column that no register row declares, so a reader "
            "looking it up in register/record.py finds nothing"
        )

    full = turn_log.get_turn("t-1")
    assert full is not None
    assert full["record"]["generated_sql"] == 'SELECT COUNT(*) FROM airline."Air Carriers"'
    assert turn_log.get_turn("nope") is None


def test_newest_first_and_a_truncated_line_does_not_hide_the_rest(turn_log) -> None:
    """One bad line must not take the log with it.

    v1's loader raised on the first unparseable file and discarded a fully paid
    69-schema build. The same shape here would make "no turns are listed" and "no turns
    were served" the same observation, which is what this whole surface exists to
    separate.
    """
    for i in range(3):
        turn_log.append_turn(_record(f"t-{i}"), question=f"q{i}")
    log = next(iter(turn_log.TURN_LOG_DIR.glob("*.jsonl")))
    with log.open("a", encoding="utf-8") as handle:
        handle.write('{"record": {"turn_id": "truncated"\n')

    turns = turn_log.list_turns()
    ids = [t["turn_id"] for t in turns]
    assert ids == ["t-2", "t-1", "t-0"], f"newest-first ordering broke: {ids}"


def test_an_incomplete_record_is_counted_against_todays_register(turn_log) -> None:
    """A turn whose record is missing a required field is not a turn that worked.

    Computed on read rather than stored, so a turn logged before a field was declared is
    judged by the declaration in force now — the question the column answers is "is this
    turn quotable", and that is a question about the current register.
    """
    from governed_bi.register.record import missing_required, required_keys

    turn_log.append_turn(_record(), question="q")
    row = turn_log.list_turns()[0]
    assert row["incomplete_fields"] == len(missing_required(_record()))
    assert row["incomplete_fields"] > 0, (
        f"the fixture record happens to be complete, so this assertion is vacuous; "
        f"required keys are {sorted(required_keys())}"
    )


def test_the_trace_stage_grouping_is_derived_from_the_register() -> None:
    """**The structural one.** No stage→fields map may be written in the route.

    Behavioural coverage cannot catch this: a hand-written map passes every test on the
    day it is written and rots silently afterwards. So the assertion is over the source
    — the route may name ``RECORD_REGISTER`` and ``field.owner``, and may not contain a
    literal list of record field names.
    """
    from pathlib import Path

    from governed_bi.register.record import RECORD_REGISTER

    source = (
        Path(__file__).resolve().parent.parent.parent
        / "src" / "governed_bi" / "api" / "routes.py"
    ).read_text(encoding="utf-8")
    body = source.split("def audit_trace(", 1)[1].split("\ndef ", 1)[0]

    assert "for field in RECORD_REGISTER" in body and "field.owner" in body, (
        "audit_trace no longer groups by the register's declared owner stage"
    )

    # Scoped to the **grouping**, not the whole function. The response envelope reads
    # three field names on purpose -- `turn_id` is the route's own parameter, `execution`
    # is where the ledger lives, `outcome` goes in the header -- and forbidding those
    # would be a rule about something else. The claim under test is narrower and is the
    # one that rots: that the stage→fields map is derived rather than written out.
    grouping = body.split("for field in RECORD_REGISTER", 1)[1].split("order = ", 1)[0]
    named = [f.name for f in RECORD_REGISTER if f'"{f.name}"' in grouping]
    assert not named, (
        f"the trace's stage grouping names record fields literally: {named}. Every one "
        "is already declared in RECORD_REGISTER with an owner stage, and a second list "
        "here is the drift register/ exists to end -- it would pass every behavioural "
        "test on the day it was written"
    )


def test_every_registered_field_reaches_a_trace_stage(turn_log) -> None:
    """The derivation, executed: no register row falls out of the trace.

    Runs the route function directly. Building a TestClient would start the app, which
    seeds a corpus from a live database — and the property under test is about the
    register, not about HTTP.
    """
    from governed_bi.api import routes

    turn_log.append_turn(_record(), question="q")
    # The route reads through the module-level store, which the fixture has repointed.
    trace = routes.audit_trace("t-1")

    assert trace["found"] is True
    traced = {f["name"] for stage in trace["stages"] for f in stage["fields"]}

    from governed_bi.register.record import RECORD_REGISTER

    declared = {f.name for f in RECORD_REGISTER}
    assert traced == declared, (
        f"the trace and the register disagree: only-in-trace={sorted(traced - declared)} "
        f"only-in-register={sorted(declared - traced)}"
    )

    # And the values are the record's, not re-derived.
    by_name = {f["name"]: f for stage in trace["stages"] for f in stage["fields"]}
    assert by_name["licensed"]["value"] == ["airline.Air_Carriers_66c534"]
    assert by_name["licensed"]["present"] is True
    assert trace["ledger"] and trace["ledger"][0]["passed"] is True


def test_a_missing_turn_says_so_rather_than_rendering_an_empty_one(turn_log) -> None:
    """``found: false`` is a value. Returning an empty record shape instead would let a
    client render a plausible page over a turn that does not exist."""
    from governed_bi.api import routes

    assert routes.audit_trace("nope")["found"] is False
    assert routes.audit_turn("nope")["found"] is False


def test_a_write_failure_is_reported_rather_than_raised(turn_log, monkeypatch) -> None:
    """A turn that answered must not become a failure because the log could not be
    written — and the caller must still be told, or "no turns are listed" and "no turns
    were served" become the same observation."""

    def boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("disk is full")

    monkeypatch.setattr(turn_log.Path, "mkdir", boom)
    turn_id, error = turn_log.append_turn(_record(), question="q")
    assert turn_id == "t-1"
    assert error is not None and "disk is full" in error


def test_the_log_holds_json_lines_and_nothing_else(turn_log) -> None:
    """One entry per line, so a tail is cheap and a partial write costs one turn."""
    turn_log.append_turn(_record("t-a"), question="a")
    turn_log.append_turn(_record("t-b"), question="b")
    log = next(iter(turn_log.TURN_LOG_DIR.glob("*.jsonl")))
    lines = [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)
        assert set(parsed) == {"asked_at", "question", "answer_text", "outcome", "record"}


def test_a_declined_turn_records_why_it_declined() -> None:
    """``outcome: "declined"`` is one value for four different engineering problems.

    ``missing_join_path`` (the join graph cannot connect the licensed tables),
    ``no_schema_matched`` (routing scored nothing), ``over_connect_bounds`` (the Steiner
    or crossing cap) and ``no_sql`` (the model never wrote a statement) are four separate
    things to go and fix. The reason lived in graph state only and never reached the
    record, so after the fact every one of them was the same row — and this surface
    exists to tell them apart.

    Asserted at ``stamp`` rather than through a served turn on purpose: forcing a decline
    end-to-end needs a corpus shaped to produce one, and the property under test is that
    the value is *carried*, not the routing arithmetic that produces it.
    """
    from governed_bi.register.record import RECORD_REGISTER, record_keys
    from governed_bi.serve.nodes.stamp import stamp

    assert "terminal_reason" in record_keys(), "the field is not declared"
    owner = next(f for f in RECORD_REGISTER if f.name == "terminal_reason")
    assert owner.absence.value == "not_applicable", (
        "a turn that answered has no terminal reason, so absence must be not_applicable "
        "rather than not_measured -- otherwise every answered turn reports a gap"
    )

    out = stamp(
        {
            "path_kind": "decline",
            "terminal_reason": "missing_join_path",
            "schemas": ["beer_factory", "shakespeare"],
            "licensed": ["beer_factory.kunden", "shakespeare.parrafos"],
            "answer": {"outcome": "declined"},
        }
    )
    record = out["answer"]["record"]
    assert record["terminal_reason"] == "missing_join_path", (
        f"the decline reason did not reach the record: {record.get('terminal_reason')!r}"
    )
    # `outcome` is left to `stamp`'s own path-signal derivation and is not asserted here.
    # It is a separate declared field with its own rules, and pinning it from this test
    # would make a change to that derivation fail a test about carrying a different value.
    from governed_bi.register.stages import Outcome

    assert record["outcome"] in {o.value for o in Outcome}
