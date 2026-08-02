"""The agent's tool calls have to survive the turn.

``_resolve_tool`` has always computed the interesting detail of every tool call —
the search query and what it found, the inspected table and whether it licensed,
the sampled table and why it was refused — and then thrown all of it at
``GovEventStream``, whose first line is ``if self._on_event is None: return``. No
eval arm passes an ``on_event``. What survived a whole run was a per-question
histogram (``n_tool_calls``): counts, no ordering, no arguments, no results. Zero
rows with ``stage`` in ``{search_corpus, inspect_schema, sample_rows}`` existed in
any ``stage_events.jsonl`` on disk.

These tests drive one real governed turn with **no** ``on_event`` attached — the
eval shape exactly — and assert the records land anyway. They go red if the
records stop being written, if they lose their detail, if they lose their order,
or if ``run_query`` starts double-counting against the ``guardrail`` / ``execute``
pair the middleware already writes for it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from governed_bi.analyst.agent import answer_question_agent
from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.eval.run_datalake import _stage_event_rows
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn
from governed_bi.stages import Stage

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"

TXN = "tbl_beer_factory_transaction"
ROUTING_NOTE = "note_beer_factory_routing"


def _trajectory():
    """One turn that touches every instrumented tool, plus both sample_rows paths."""
    return [
        ai_tool_turn("search_corpus", {"query": "total revenue"}, "c0"),
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn("read_notes", {"note_id": ROUTING_NOTE}, "c2"),
        ai_tool_turn("grep_notes", {"pattern": "ZipCode"}, "c3"),
        # Denied before the guardrail runs: no `guardrail`/`execute` pair is written
        # for this call, so its own record is the only trace it ever leaves.
        ai_tool_turn("sample_rows", {"table_id": "tbl_does_not_exist", "n": 3}, "c4"),
        # Licensed by c1, so this one DOES reach the guardrail and execute.
        ai_tool_turn("sample_rows", {"table_id": TXN, "n": 3}, "c5"),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
            "c6",
        ),
        AIMessage(content="done"),
    ]


@pytest.fixture
def served(tmp_path):
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    try:
        yield answer_question_agent(
            "total revenue",
            Identity(user="dev", all_access=True),
            corpus=load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst(),
            gateway=Gateway(conn),
            settings=replace(
                Settings.for_env(Environment.dev),
                run_log_kind="sqlite",
                run_log_path=str(tmp_path / "runs.sqlite"),
            ),
            session_id="tool-stages",
            model=FakeToolModel(responses=_trajectory()),
            # No on_event: the eval shape. Every assertion below therefore proves the
            # DURABLE sink, not the live stream.
        )
    finally:
        conn.close()


def _by_stage(served, stage: Stage) -> list[dict]:
    return [e for e in served.provenance["stage_events"] if e["stage"] == stage.value]


def test_every_exploration_tool_call_leaves_a_durable_record(served):
    seen = {e["stage"] for e in served.provenance["stage_events"]}
    assert {
        Stage.search_corpus.value,
        Stage.inspect_schema.value,
        Stage.read_notes.value,
        Stage.grep_notes.value,
    } <= seen


def test_the_search_query_and_its_hit_counts_are_on_the_record(served):
    (event,) = _by_stage(served, Stage.search_corpus)
    assert event["detail"]["query"] == "total revenue"
    # A count, not just the fact that a search happened: "the agent searched seven
    # times and found nothing" is the interesting trajectory, and the histogram
    # could not express it.
    assert isinstance(event["detail"]["tables"], int)
    assert isinstance(event["ms"], float)


def test_the_inspected_table_and_whether_it_licensed_are_on_the_record(served):
    (event,) = _by_stage(served, Stage.inspect_schema)
    assert event["detail"]["table_id"] == TXN
    assert event["detail"]["licensed"] is True
    assert event["detail"]["columns"] > 0


def test_the_note_tools_record_what_they_returned(served):
    (read,) = _by_stage(served, Stage.read_notes)
    assert read["detail"]["note_id"] == ROUTING_NOTE
    assert (read["detail"]["found"], read["detail"]["withheld"]) == (True, False)

    (grep,) = _by_stage(served, Stage.grep_notes)
    assert grep["detail"]["pattern"] == "ZipCode"
    assert grep["detail"]["hits"] >= 1
    assert grep["detail"]["pattern_error"] is False


def test_a_pre_guardrail_sample_denial_is_the_only_sample_rows_record(served):
    """Both halves of the no-double-count rule, in one assertion pair.

    A ``sample_rows`` that reaches the guardrail already produces a ``guardrail`` +
    ``execute`` pair stamped ``action="sample_rows"``; a third record would
    double-count it. A ``sample_rows`` denied by the licensing check returns before
    ``check()`` runs and produces neither, so it needs one.
    """
    (denied,) = _by_stage(served, Stage.sample_rows)
    assert denied["detail"]["denied"] is True
    assert denied["detail"]["table_id"] == "tbl_does_not_exist"
    assert "not available" in denied["detail"]["reason"]

    actions = [e["detail"].get("action") for e in _by_stage(served, Stage.guardrail)]
    assert actions == ["sample_rows", "run_query"]


def test_run_query_is_not_double_counted(served):
    """``run_query`` gets no record of its own — the middleware writes ``guardrail``
    + ``execute`` for it, and the ledger and every rate derived from it already
    agree on that count."""
    stages = [e["stage"] for e in served.provenance["stage_events"]]
    assert "run_query" not in stages
    run_query_guardrails = [
        e for e in _by_stage(served, Stage.guardrail) if e["detail"].get("action") == "run_query"
    ]
    assert len(run_query_guardrails) == 1
    assert len(_by_stage(served, Stage.execute)) == 2  # the sample + the query


def test_the_records_carry_an_explicit_order(served):
    """File order is the only ordering ``stage_events.jsonl`` had, and concurrent
    workers interleave into one file — so a trajectory could not be reconstructed
    from disk at all. ``seq`` is that order, stamped by the producer."""
    events = served.provenance["stage_events"]
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(len(events)))

    order = {e["stage"]: e["seq"] for e in events}
    assert (
        order[Stage.search_corpus.value]
        < order[Stage.inspect_schema.value]
        < order[Stage.read_notes.value]
        < order[Stage.grep_notes.value]
        < order[Stage.sample_rows.value]
    )


def test_the_tool_records_reach_the_eval_artifact_with_their_detail(served):
    """The whole chain, since every hop of it is otherwise tested against a fixture:
    serve provenance -> the driver's flattener -> a ``stage_events.jsonl`` row."""
    rows = _stage_event_rows(
        served.provenance, question_id="q1", arm="curated", db_id="beer_factory"
    )
    search = [r for r in rows if r["stage"] == Stage.search_corpus.value]
    assert search and search[0]["detail"]["query"] == "total revenue"
    assert search[0]["seq"] == next(
        e["seq"] for e in served.provenance["stage_events"]
        if e["stage"] == Stage.search_corpus.value
    )


def test_the_portable_log_still_keeps_numbers_only(served, tmp_path):
    """The new records carry the model's own search string. The portable run log is
    metadata-only by contract, and it must stay that way without needing to know
    that ``detail["query"]`` now exists."""
    from governed_bi.analyst.run_log import strip_stage_events_for_log

    stripped = strip_stage_events_for_log(served.provenance["stage_events"])
    assert "total revenue" not in str(stripped)
    assert "ZipCode" not in str(stripped)
    for event in stripped:
        assert all(
            isinstance(v, (bool, int, float)) for v in event["detail"].values()
        ), event
    # …and the ordering survives the projection, so a stripped record is still a
    # trajectory rather than a bag.
    assert [e["seq"] for e in stripped] == list(
        range(len(served.provenance["stage_events"]))
    )
