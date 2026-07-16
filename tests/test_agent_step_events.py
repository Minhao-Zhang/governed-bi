"""Agent step-event stream (backend for the UI timeline).

Covers the ``GovEventStream`` emitter contract and the end-to-end event trace the
agent serve path streams, per docs/plans/agent-step-visualization.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn
from governed_bi.analyst.agent import answer_question_agent
from governed_bi.analyst.answer import UncertaintySignals, assemble, refusal
from governed_bi.analyst.governance import GovEventStream

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"
TXN = "tbl_beer_factory_transaction"


# --------------------------------------------------------------------------- #
# GovEventStream unit contract
# --------------------------------------------------------------------------- #


def test_seq_is_monotonic_and_serve_path_tags_first_only():
    out: list[dict] = []
    s = GovEventStream(out.append)
    s.rail("route", intent="lookup")
    s.rail("refuse_gate", "ok")
    s.tool("run_query", "ok", step_id="c1", attempt=1, sql="SELECT 1")

    assert [e["seq"] for e in out] == [0, 1, 2]
    assert out[0]["serve_path"] == "agent"
    assert "serve_path" not in out[1]
    assert "serve_path" not in out[2]
    assert out[0]["kind"] == "rail" and out[0]["step"] == "route"
    assert out[0]["detail"]["intent"] == "lookup"
    assert out[2]["kind"] == "tool" and out[2]["id"] == "c1"
    assert out[2]["detail"] == {"attempt": 1, "sql": "SELECT 1"}


def test_reset_starts_a_fresh_turn():
    out: list[dict] = []
    s = GovEventStream(out.append)
    s.rail("route")
    s.reset()
    s.rail("route")

    assert out[0]["seq"] == 0
    assert out[1]["seq"] == 0
    # serve_path re-tags the first event after a reset
    assert out[1]["serve_path"] == "agent"


def test_none_values_are_dropped_from_detail():
    out: list[dict] = []
    s = GovEventStream(out.append)
    s.tool("run_query", "blocked", step_id="c1", sql="SELECT 1", layer=None, rows=None)
    assert out[0]["detail"] == {"sql": "SELECT 1"}


def test_final_maps_both_axes_for_a_delivered_answer():
    out: list[dict] = []
    s = GovEventStream(out.append)
    ans = assemble(
        text="x",
        sql="SELECT 1",
        signals=UncertaintySignals(repaired=True),
        provenance={"tables_used": ["t"], "min_join_confidence": 1.0},
    )
    s.final(ans)

    e = out[0]
    assert e["kind"] == "final" and e["step"] == "finalize" and e["status"] == "ok"
    assert e["detail"]["semantic_assurance"] == "heuristic"  # repaired → lineage/heuristic
    assert e["detail"]["tier"] == "lineage"
    assert e["detail"]["safety_clearance"] is True
    assert e["detail"]["tables_used"] == ["t"]


def test_final_is_refused_for_a_refusal():
    out: list[dict] = []
    s = GovEventStream(out.append)
    s.final(refusal(escalation="nope"))
    assert out[0]["status"] == "refused"
    assert out[0]["detail"]["semantic_assurance"] == "none"
    assert out[0]["detail"]["tier"] == "refused"


def test_emitter_is_best_effort():
    # No callback → no-op, no crash.
    GovEventStream(None).rail("route")

    def boom(_payload):
        raise RuntimeError("sink down")

    GovEventStream(boom).final(refusal(escalation="x"))  # must not raise


# --------------------------------------------------------------------------- #
# End-to-end event trace over a scripted trajectory (repair loop)
# --------------------------------------------------------------------------- #


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


@pytest.fixture
def settings():
    return Settings.for_env(Environment.dev)


@pytest.fixture
def identity():
    return Identity(user="dev", all_access=True)


@pytest.fixture
def bird_gateway():
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    yield Gateway(conn)
    conn.close()


def test_agent_path_streams_rail_tool_and_final_events(
    corpus, bird_gateway, settings, identity
):
    events: list[dict] = []
    turns = [
        ai_tool_turn("search_corpus", {"query": "total revenue"}, "c0"),
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        # attempt 1: an unlicensed table → L4 term_semantics block (repairable)
        ai_tool_turn("run_query", {"sql": 'SELECT "StarRating" FROM "rootbeerreview"'}, "c2"),
        # attempt 2: the licensed table → passes
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
            "c3",
        ),
        AIMessage(content="done"),
    ]
    # The bare question keeps rootbeerreview outside the seeded license scope, so
    # attempt 1 (below) reliably blocks at L4; a wordier phrasing widens retrieval.
    ans = answer_question_agent(
        "total revenue",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="step-events",
        model=FakeToolModel(responses=turns),
        on_event=events.append,
    )

    # Renderer selection + ordering.
    assert events[0].get("serve_path") == "agent"
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)

    # Outer rails.
    rails = {(e["step"], e["status"]) for e in events if e["kind"] == "rail"}
    assert ("route", "ok") in rails
    assert ("refuse_gate", "ok") in rails
    assert ("assemble", "ok") in rails

    # Tool starts pair with resolves by id.
    starts = {e["id"] for e in events if e.get("status") == "start"}
    assert {"c1", "c2", "c3"} <= starts

    # inspect_schema resolved as licensed with a column count.
    insp = [
        e
        for e in events
        if e["kind"] == "tool" and e["step"] == "inspect_schema" and e["status"] != "start"
    ]
    assert insp and insp[0]["detail"]["licensed"] is True
    assert insp[0]["detail"]["columns"] > 0

    # run_query repair loop: attempt 1 blocked (term_semantics), attempt 2 ok.
    rq = [
        e
        for e in events
        if e["kind"] == "tool" and e["step"] == "run_query" and e["status"] != "start"
    ]
    blocked = [e for e in rq if e["status"] == "blocked"]
    passed = [e for e in rq if e["status"] == "ok"]
    assert blocked and blocked[0]["detail"]["attempt"] == 1
    assert blocked[0]["detail"]["layer"] == "term_semantics"
    assert passed and passed[-1]["detail"]["attempt"] == 2
    assert passed[-1]["detail"]["rows"] is not None

    # Exactly one terminal final event, carrying the answer stamp.
    finals = [e for e in events if e["kind"] == "final"]
    assert len(finals) == 1
    assert finals[-1]["detail"]["safety_clearance"] is True
    assert finals[-1]["detail"]["semantic_assurance"] in ("heuristic", "grounded")

    # The event trace's run_query entries equal the audit ledger (Inv #10).
    ledger = ans.provenance.get("governance_ledger") or []
    ledger_rq = [x for x in ledger if x.get("action") == "run_query"]
    assert len(ledger_rq) == len(rq)


def test_negative_example_refusal_stops_at_the_gate(
    corpus, bird_gateway, settings, identity
):
    events: list[dict] = []
    negatives = [a for a in corpus.assets if type(a).__name__ == "NegativeExampleAsset"]
    if not negatives:
        pytest.skip("no negative examples in the corpus")
    question = negatives[0].example_questions[0]

    answer_question_agent(
        question,
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="neg",
        model=FakeToolModel(responses=[AIMessage(content="unused")]),
        on_event=events.append,
    )

    steps = [e["step"] for e in events]
    assert ("refuse_gate", "refused") in {(e["step"], e["status"]) for e in events if e["kind"] == "rail"}
    # Stopped at the gate: no tool activity, no assemble.
    assert "assemble" not in steps
    assert not any(e["kind"] == "tool" for e in events)
    finals = [e for e in events if e["kind"] == "final"]
    assert finals and finals[-1]["status"] == "refused"
