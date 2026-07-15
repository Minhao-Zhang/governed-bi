"""Serve-time clarification (HITL) — offline end-to-end tests.

Drives the real chat graph (api/graph_app.py -> answer_question_agent -> agent
core) through the ask_user interrupt/resume round trip, using a scripted
FakeToolModel instead of a live model. Verifies the wire contract
(docs/plans/hitl-clarification-contract.md): the ClarificationRequest surfaces as
the outer graph's __interrupt__ value, stream.respond/Command(resume) continues to
a governed answer, provenance records the clarification, and a decline fails closed.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from governed_bi.api.graph_app import build_chat_graph  # noqa: E402
from governed_bi.api.stack import ServeStack  # noqa: E402
from governed_bi.config import Environment, Settings  # noqa: E402
from governed_bi.corpus import load_corpus  # noqa: E402
from governed_bi.gateway import Identity  # noqa: E402
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn  # noqa: E402

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"
REVENUE_Q = "What is the total revenue?"


def _clarify_stack(turns: list) -> ServeStack:
    """A live-ish stack: scripted model + an in-memory clarify checkpointer, so
    ask_user's interrupt can pause/resume (as build_stack wires for real)."""
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    corpus_full = load_corpus(CORPUS_ROOT, schema="beer_factory")
    return ServeStack(
        corpus_full=corpus_full,
        corpus_server=corpus_full.for_server(),
        settings=Settings.for_env(Environment.dev),
        dialect="sqlite",
        sqlite_path=BIRD_DB,
        identity=Identity(user="demo", all_access=True),
        embedder=None,
        narrator=None,
        model_name="fake",
        has_live_model=True,
        chat_model=FakeToolModel(responses=turns),
        can_clarify=True,
        clarify_checkpointer=InMemorySaver(),
    )


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


# A trajectory that asks one clarification, then answers the governed way.
_ANSWER_TURNS = [
    ai_tool_turn(
        "ask_user",
        {"question": "Revenue gross or net?", "why": "two revenue definitions exist"},
        "a1",
    ),
    ai_tool_turn("inspect_schema", {"table_id": "tbl_beer_factory_transaction"}, "a2"),
    ai_tool_turn(
        "run_query",
        {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
        "a3",
    ),
    AIMessage(content="done"),
]


def test_ask_user_surfaces_clarification_request_as_interrupt():
    stack = _clarify_stack(_ANSWER_TURNS)
    graph = build_chat_graph(stack, checkpointer=InMemorySaver())

    result = graph.invoke({"messages": [HumanMessage(REVENUE_Q)]}, _cfg("c1"))

    assert "__interrupt__" in result, "the turn should pause on ask_user"
    req = result["__interrupt__"][0].value
    # Wire contract §3.
    assert req["kind"] == "clarification"
    assert req["question"] == "Revenue gross or net?"
    assert req["why"] == "two revenue definitions exist"
    assert req["clarification_id"].startswith("clar_")
    assert req["tier"] == "audit"


def test_resume_continues_to_governed_answer_with_provenance():
    stack = _clarify_stack(_ANSWER_TURNS)
    graph = build_chat_graph(stack, checkpointer=InMemorySaver())
    cfg = _cfg("c2")

    first = graph.invoke({"messages": [HumanMessage(REVENUE_Q)]}, cfg)
    req = first["__interrupt__"][0].value

    resumed = graph.invoke(
        Command(resume={"clarification_id": req["clarification_id"], "answer": "gross"}),
        cfg,
    )

    answer = resumed["answer"]
    assert answer["tier"] == "governed"
    assert "SUM" in (answer["sql"] or "").upper()
    # Provenance records the answered clarification (contract §7).
    clar = answer["provenance"]["clarifications"]
    assert clar and clar[0]["answer"] == "gross"
    assert clar[0]["answered_by"] == "user"
    # The turn actually finished (outer graph no longer paused).
    assert not graph.get_state(cfg).next
    # Idempotency (langgraph HITL best practice): the node re-runs on resume, but
    # the inner agent replays completed steps from its checkpointer rather than
    # re-executing them — so the guarded run_query appears exactly once, not twice.
    ledger = answer["provenance"].get("governance_ledger") or []
    runs = [e for e in ledger if e.get("action") == "run_query"]
    assert len(runs) == 1, f"run_query should execute once across resume, got {len(runs)}"


def test_decline_fails_closed():
    stack = _clarify_stack(_ANSWER_TURNS)
    graph = build_chat_graph(stack, checkpointer=InMemorySaver())
    cfg = _cfg("c3")

    first = graph.invoke({"messages": [HumanMessage(REVENUE_Q)]}, cfg)
    req = first["__interrupt__"][0].value

    resumed = graph.invoke(
        Command(resume={"clarification_id": req["clarification_id"], "declined": True}),
        cfg,
    )

    answer = resumed["answer"]
    assert answer["tier"] == "refused"
    assert answer["sql"] is None
    assert answer["provenance"]["refused_by"] == "clarification_declined"


# Two clarifications in one turn, then answer.
_MULTI_TURNS = [
    ai_tool_turn(
        "ask_user", {"question": "Gross or net revenue?", "why": "two definitions"}, "m1"
    ),
    ai_tool_turn(
        "ask_user", {"question": "Which fiscal year?", "why": "no year given"}, "m2"
    ),
    ai_tool_turn("inspect_schema", {"table_id": "tbl_beer_factory_transaction"}, "m3"),
    ai_tool_turn(
        "run_query",
        {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
        "m4",
    ),
    AIMessage(content="done"),
]


def test_sequential_multi_clarification():
    """Two ask_user calls in one turn: each pauses, each resumes, then the turn
    finishes — and both land in provenance while run_query stays idempotent."""
    stack = _clarify_stack(_MULTI_TURNS)
    graph = build_chat_graph(stack, checkpointer=InMemorySaver())
    cfg = _cfg("multi")

    first = graph.invoke({"messages": [HumanMessage(REVENUE_Q)]}, cfg)
    req_a = first["__interrupt__"][0].value
    assert req_a["question"] == "Gross or net revenue?"

    # Answer the first — the turn must pause AGAIN on the second question.
    second = graph.invoke(
        Command(resume={"clarification_id": req_a["clarification_id"], "answer": "gross"}),
        cfg,
    )
    assert "__interrupt__" in second, "should pause again on the second ask_user"
    req_b = second["__interrupt__"][0].value
    assert req_b["question"] == "Which fiscal year?"
    assert req_b["clarification_id"] != req_a["clarification_id"]

    # Answer the second — now the turn completes.
    final = graph.invoke(
        Command(resume={"clarification_id": req_b["clarification_id"], "answer": "2023"}),
        cfg,
    )
    answer = final["answer"]
    assert answer["tier"] == "governed"
    # Both clarifications recorded in provenance (contract §7).
    clar = answer["provenance"]["clarifications"]
    assert {c["answer"] for c in clar} == {"gross", "2023"}
    # Idempotent across two resumes: run_query executed exactly once.
    ledger = answer["provenance"].get("governance_ledger") or []
    assert len([e for e in ledger if e.get("action") == "run_query"]) == 1


def test_no_ask_user_tool_when_clarify_disabled():
    """Parity: with no clarify checkpointer (the eval/offline path), the agent has
    no ask_user tool and the turn never interrupts."""
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": "tbl_beer_factory_transaction"}, "b1"),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
            "b2",
        ),
        AIMessage(content="done"),
    ]
    stack = replace(
        _clarify_stack(turns), can_clarify=False, clarify_checkpointer=None
    )
    graph = build_chat_graph(stack)  # no outer checkpointer, like today
    result = graph.invoke({"messages": [HumanMessage(REVENUE_Q)]}, _cfg("b"))

    assert "__interrupt__" not in result
    assert result["answer"]["tier"] == "governed"
