"""Tests for the LangGraph Server chat graph (rework phase 2; api/graph_app.py).

The graph wraps the governed flow in a thin {messages, answer} chat shell. These
assert it answers, streams stage events, stays Answer-equivalent to a direct
answer_question call, and rebuilds working memory from the thread. Gated on
langgraph (the agents extra); the executed cases use the committed beer_factory
DB (skipped if absent).
"""

from __future__ import annotations

from dataclasses import asdict
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
from governed_bi.gateway import Gateway, Identity, SqliteConnector  # noqa: E402
from governed_bi.server import answer_question  # noqa: E402
from governed_bi.viz import presenter  # noqa: E402

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"

REVENUE_Q = "What is the total revenue?"


@pytest.fixture
def stack():
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    corpus_full = load_corpus(CORPUS_ROOT, db="beer_factory")
    return ServeStack(
        corpus_full=corpus_full,
        corpus_server=corpus_full.for_server(),
        settings=Settings.for_env(Environment.dev),
        dialect="sqlite",
        sqlite_path=BIRD_DB,
        identity=Identity(user="demo", all_access=True),
        generator=None,
        embedder=None,
        narrator=None,
        model_name=None,
        has_live_model=False,
    )


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


# --------------------------------------------------------------------------- #
# Pure helpers
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
# Invoke
# --------------------------------------------------------------------------- #


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


def test_invoke_refusal_has_no_sql(stack):
    graph = build_chat_graph(stack)
    result = graph.invoke(
        {"messages": [HumanMessage("How many employees work at the factory?")]}, _cfg("t2")
    )
    assert result["answer"]["tier"] == "refused"
    assert result["answer"]["sql"] is None
    assert result["answer"]["escalation"]


# --------------------------------------------------------------------------- #
# Streaming stage events
# --------------------------------------------------------------------------- #


def test_stream_emits_labeled_stage_events(stack):
    graph = build_chat_graph(stack)
    stages = []
    for mode, data in graph.stream(
        {"messages": [HumanMessage(REVENUE_Q)]}, _cfg("t3"), stream_mode=["updates", "custom"]
    ):
        if mode == "custom":
            stages.append(data["stage"])
    assert stages == ["route", "retrieve", "generate", "guardrail", "execute", "compose"]


# --------------------------------------------------------------------------- #
# Equivalence with the direct flow
# --------------------------------------------------------------------------- #


def test_graph_answer_equals_direct_answer_question(stack):
    graph = build_chat_graph(stack)
    graph_answer = graph.invoke({"messages": [HumanMessage(REVENUE_Q)]}, _cfg("teq"))["answer"]

    conn = SqliteConnector(BIRD_DB)
    try:
        direct = answer_question(
            REVENUE_Q,
            stack.identity,
            corpus=stack.corpus_server,
            gateway=Gateway(conn),
            settings=stack.settings,
            session_id="teq",
        )
    finally:
        conn.close()
    assert graph_answer == asdict(presenter.answer_view(direct))


# --------------------------------------------------------------------------- #
# Working memory from the thread
# --------------------------------------------------------------------------- #


def test_prior_turns_are_replayed_as_working_memory(stack):
    # A multi-turn thread: the node answers the last human turn (a follow-up) with
    # the earlier turns rebuilt as working memory, and still returns a governed
    # answer (template path). This exercises _split + _working_memory_from.
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
