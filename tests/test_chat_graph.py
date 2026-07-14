"""Tests for the LangGraph Server chat graph (api/graph_app.py).

The graph wraps the governed agentic serve core in a thin {messages, answer} chat
shell. Answering a turn now requires a live model (agent-only serve, ADR 0002),
so the end-to-end invoke/stream cases are live-only and skipped in the hermetic
suite — offline coverage of the agent rails lives in the agent tests, and live
coverage in scripts/live_smoke.py. The pure message-splitting helpers stay
offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from governed_bi.api.graph_app import (  # noqa: E402
    _split_question_and_history,
    build_chat_graph,
)
from governed_bi.api.stack import ServeStack  # noqa: E402
from governed_bi.config import Environment, Settings  # noqa: E402
from governed_bi.corpus import load_corpus  # noqa: E402
from governed_bi.gateway import Identity  # noqa: E402

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"

REVENUE_Q = "What is the total revenue?"

# Answering a turn drives the agent core, which needs a live model; the hermetic
# suite has none, so these are opt-in (set GOVERNED_BI_LIVE_SERVE + a real key and
# disable the conftest strip to run them).
requires_live_serve = pytest.mark.skip(
    reason="agent-only serve needs a live model; covered by scripts/live_smoke.py"
)


@pytest.fixture
def stack():
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
        model_name=None,
        has_live_model=False,
    )


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


# --------------------------------------------------------------------------- #
# Pure helpers (offline)
# --------------------------------------------------------------------------- #


def test_split_question_and_history_picks_last_human():
    messages = [
        HumanMessage("first question"),
        AIMessage("first answer"),
        HumanMessage("current question"),
    ]
    question, history = _split_question_and_history(messages)
    assert question == "current question"
    assert history == messages[:2]


def test_split_question_raises_without_human():
    with pytest.raises(ValueError):
        _split_question_and_history([AIMessage("orphan")])


# --------------------------------------------------------------------------- #
# Graph wiring smoke (offline via FakeToolModel — this is the `langgraph dev`
# graphs.serve path: chat graph node → answer_question_agent → agent core →
# {messages, answer}). A scripted trajectory stands in for a live model so a
# wiring regression is caught in CI without a key; answer QUALITY stays live-only.
# --------------------------------------------------------------------------- #


def test_graph_answers_governed_turn_with_fake_model(stack):
    from dataclasses import replace

    from governed_bi.llm.fake import FakeToolModel, ai_tool_turn

    turns = [
        ai_tool_turn("inspect_schema", {"table_id": "tbl_beer_factory_transaction"}, "c1"),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
            "c2",
        ),
        AIMessage(content="done"),
    ]
    live = replace(stack, chat_model=FakeToolModel(responses=turns), has_live_model=True)
    graph = build_chat_graph(live)
    result = graph.invoke({"messages": [HumanMessage(REVENUE_Q)]}, _cfg("wiring"))

    assert result["answer"]["tier"] == "governed"
    sql = result["answer"]["sql"] or ""
    # SQL is normalized (quoted identifiers) by the middleware, so match loosely.
    assert "SUM" in sql.upper() and "PurchasePrice" in sql
    last = result["messages"][-1]
    assert isinstance(last, AIMessage)
    assert last.additional_kwargs["governed_bi"]["tier"] == "governed"
    assert last.content  # rendered answer text


# --------------------------------------------------------------------------- #
# End-to-end serve turn (live-only: needs a real model)
# --------------------------------------------------------------------------- #


@requires_live_serve
def test_invoke_returns_governed_answer_and_message(stack):
    graph = build_chat_graph(stack)
    result = graph.invoke({"messages": [HumanMessage(REVENUE_Q)]}, _cfg("t1"))

    assert result["answer"]["tier"] == "governed"
    assert result["answer"]["safety_clearance"] is True
    assert "SUM(PurchasePrice)" in result["answer"]["sql"]

    last = result["messages"][-1]
    assert isinstance(last, AIMessage)
    assert last.additional_kwargs["governed_bi"]["tier"] == "governed"
    assert last.content  # the English answer text


@requires_live_serve
def test_invoke_refusal_has_no_sql(stack):
    graph = build_chat_graph(stack)
    result = graph.invoke(
        {"messages": [HumanMessage("How many employees work at the factory?")]}, _cfg("t2")
    )
    assert result["answer"]["tier"] == "refused"
    assert result["answer"]["sql"] is None
    assert result["answer"]["escalation"]


@requires_live_serve
def test_prior_turns_are_replayed_as_working_memory(stack):
    graph = build_chat_graph(stack)
    result = graph.invoke(
        {
            "messages": [
                HumanMessage(REVENUE_Q),
                AIMessage("total_revenue = 18496.0"),
                HumanMessage("What is the average star rating?"),
            ]
        },
        _cfg("t4"),
    )
    assert result["answer"]["tier"] == "governed"
    assert "AVG(StarRating)" in result["answer"]["sql"]
