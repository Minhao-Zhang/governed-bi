"""A turn that dies with a tool call pending must not take the whole thread with it.

**The defect.** ``agent_core_node`` commits the last *committed* values frame's new messages to
the outer ``messages`` channel whatever happened — and when the loop died between the model
asking for a tool and the tools super-step yielding a frame, that frame is an ``AIMessage``
carrying ``tool_calls`` and no ``ToolMessage``. The next turn's inbound **is** that channel, so
the dangling ``tool_use`` is replayed at the head of every later turn, forever. Bedrock rejects
a history with an unanswered ``tool_use``, so the casualty is the conversation, not the turn.

This repo has already paid for it once by the other route in: thread ``01a01bb8`` (2026-08-19),
12 ``tool_use`` against 11 ``tool_result``, every subsequent turn raising ``ValidationException``
before reaching a single attempt. ``tests/serve/test_the_attempt_cap_ends_the_turn.py`` holds
that route — the attempt cap — and until this file the invariant was enforced *only* there.
What makes the routes below worse is that the damaging turn stamps an ordinary ``crashed`` and
reads like any other failure.

Three paths reach it, and each has a test here: the ``agent_node_timeout_s`` wall (a live
production knob), any exception out of the nested agent with a call pending, and ``ResumeRejected``
from a resume by the wrong caller.

**And the one thing the repair must not do.** A turn paused at ``interrupt()`` has an unanswered
``tool_use`` *by design*; answering it would break resume, which is the feature the clarification
surface exists for. Today that is structural — ``_run`` re-raises ``GraphInterrupt`` and the node
calls it inside no ``try``, so a pause never reaches the write — and the last two tests are what
would notice a refactor that catches ``GraphInterrupt`` in the node and quietly turns this fix
into a resume-breaker.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command

from governed_bi.corpus.analyst import analyst_corpus_from_keys
from governed_bi.govern.check import GovernanceUsageError
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve import graph as graph_module
from governed_bi.serve.nodes import agent_core
from governed_bi.serve.nodes.agent_core import agent_core_node
from governed_bi.serve.resume import CALLER_KEY
from governed_bi.serve.scripted_model import ScriptedChatModel

ASKED = "analyst-7"


def _ask_user(*ids: str) -> AIMessage:
    """One assistant message asking ``ask_user`` once per id. Needs no database."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ask_user",
                "args": {"question": f"which year, take {n}?"},
                "id": call_id,
                "type": "tool_call",
            }
            for n, call_id in enumerate(ids, start=1)
        ],
    )


RUN_QUERY = AIMessage(
    content="",
    tool_calls=[
        {
            "name": "run_query",
            "args": {"sql": "SELECT COUNT(*) AS n FROM beer_factory.customers"},
            "id": "c1",
            "type": "tool_call",
        }
    ],
)


class _Stub:
    """A connector that answers without a database, as in ``test_agent_core_partial_ledger``."""

    dialect = "postgres"

    def execute(self, sql: str, **_: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        return (["n"], [(3,)], False)


def _state() -> dict[str, Any]:
    return {
        "question": "how many customers?",
        "thread_id": "t-strand",
        "turn_index": 1,
        "turn_id": "turn-strand",
        "run_id": "r",
        "question_id": "q",
        "db_id": "beer_factory",
        "attempt_id": "a",
        "corpus_content_hash": "c",
        "prompt_set_hash": "p",
        "knobs_resolved": {},
        "n_re_served": 0,
        # Routing has to succeed or the turn refuses before ``agent_core`` runs at all.
        "facet_route_hits": [("facet_schema", "beer_factory", 1.0)],
        "messages": [],
        "usage": [],
        "clarifications": [],
        "licensed": ["beer_factory.customers"],
        "identity": {"token": ASKED},
    }


def _config(model: Any, *, caller: str = ASKED) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": "t-strand",
            # No guard rules: these tests are about the message list, not about governance.
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
            "connector": _Stub(),
            "corpus": analyst_corpus_from_keys(allowed=["beer_factory.customers.id"]),
            CALLER_KEY: caller,
        }
    }


def _dangling(messages: Any) -> dict[str, str]:
    """``{tool_call_id: tool name}`` for every ``tool_use`` nothing answered.

    The same computation ``test_the_attempt_cap_ends_the_turn`` makes, and the reason both files
    make it: this set being non-empty is not a cosmetic defect in a message list, it is a
    conversation that cannot be replayed.
    """
    asked = {
        str(call["id"]): str(call.get("name") or "")
        for message in messages or ()
        if isinstance(message, AIMessage)
        for call in message.tool_calls or ()
    }
    answered = {
        str(getattr(message, "tool_call_id", ""))
        for message in messages or ()
        if str(getattr(message, "type", "")) == "tool"
    }
    return {k: v for k, v in asked.items() if k not in answered}


def _turn(model: Any) -> dict[str, Any]:
    """The node on its own. Enough for the two paths that need no checkpointer."""
    return asyncio.run(agent_core_node(_state(), _config(model)))


# ── path 1: the soft wall fires with a tool call pending ──────────────────────────────


class _SlowModel(ScriptedChatModel):
    """A model call slower than the wall, so the wall fires on the frame after it.

    ``agent_node_timeout_s`` is checked **between** frames (``_run`` says why it must be), so the
    check that ends this turn is the one after the post-model frame has been consumed and before
    the tools super-step yields anything. That window — model has asked, tools have not answered
    — is the whole defect, and it is reachable from a live production knob.
    """

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        time.sleep(0.4)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_a_timeout_between_the_ask_and_the_tool_answers_the_pending_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOVERNED_BI_AGENT_NODE_TIMEOUT_S", "0.05")
    out = _turn(_SlowModel(responses=[_ask_user("c1")]))

    assert out["path_kind"] == "crashed"
    assert out["failure"] == {"stage": "agent_core", "error_type": "TimeoutError"}
    messages = out["messages"]
    assert not _dangling(messages), (
        f"unanswered tool_use {sorted(_dangling(messages))} committed to the thread; every later "
        "turn replays this history, so the thread is dead rather than the turn"
    )
    reply = messages[-1]
    assert getattr(reply, "status", None) == "error", (
        "a reply without error status reads as a result the tool produced"
    )
    assert "TimeoutError" in str(reply.content), (
        f"the reply is replayed to a model and has to say what happened, got {reply.content!r}"
    )


def test_both_calls_of_one_stranded_batch_are_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two calls to the *same* tool in one message — the shape that cost thread ``01a01bb8``.

    The repair keys on whether a call was answered, never on the tool's name: an earlier version
    filtered by name and looked straight past the sibling that shared it. One unanswered id is
    as fatal as twelve.
    """
    monkeypatch.setenv("GOVERNED_BI_AGENT_NODE_TIMEOUT_S", "0.05")
    out = _turn(_SlowModel(responses=[_ask_user("c1", "c2")]))

    assert out["path_kind"] == "crashed"
    assert not _dangling(out["messages"]), (
        f"unanswered tool_use {sorted(_dangling(out['messages']))}; a name-keyed filter strands "
        "the second call to a tool it has already seen"
    )


# ── path 2: an exception out of the nested agent, with the call pending ───────────────


def test_a_wiring_failure_raised_out_of_a_tool_answers_the_pending_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GovernanceUsageError`` is the production instance, and it is re-raised on purpose.

    ``serve/tools.py``'s ``run_query`` converts an ordinary tool exception into a reply and a
    ledger row, but re-raises this one — a security parameter that was never wired up (G1) must
    not come back as an error string that reads like the tool failing on the model's input. So it
    leaves the nested agent as an exception with the ``tool_use`` still pending. A
    ``GraphRecursionError`` — raised by exploration tools that ``_CapEndsTheTurn`` does not count
    — arrives at the same place by the same route.
    """
    from governed_bi.serve import fetch

    def _unwired(*_a: Any, **_k: Any) -> tuple[str, dict[str, Any]]:
        raise GovernanceUsageError("bounds were never wired up")

    monkeypatch.setattr(fetch, "run_query", _unwired)
    out = _turn(ScriptedChatModel(responses=[RUN_QUERY, AIMessage("three customers")]))

    assert out["path_kind"] == "crashed"
    assert out["failure"] == {"stage": "agent_core", "error_type": "GovernanceUsageError"}
    assert not _dangling(out["messages"]), (
        f"unanswered tool_use {sorted(_dangling(out['messages']))} after a wiring failure; the "
        "turn is meant to fail, the thread is not"
    )
    assert "GovernanceUsageError" in str(out["messages"][-1].content)


# ── path 3: a resume the identity gate refuses ───────────────────────────────────────


def _paused(graph: Any, model: Any) -> dict[str, Any]:
    paused = graph.invoke(_state(), _config(model))
    assert paused.get("__interrupt__"), "precondition: ask_user did not pause the turn"
    return paused


def test_a_resume_by_the_wrong_caller_does_not_poison_the_thread() -> None:
    """``ResumeRejected`` is raised on the line ``interrupt()`` returns on (ADR 0006 B9).

    Latent while there is one principal and live the moment a fork has two — and it lands in the
    worst possible place, because the ``ask_user`` ``tool_use`` is already in the frame and the
    answer that would have closed it is exactly what the gate is withholding. The refusal must
    cost the hijacker the turn, not cost the victim the thread.
    """
    graph = graph_module.compile_graph()
    model = ScriptedChatModel(responses=[_ask_user("c1"), AIMessage("ok: 2020")])
    _paused(graph, model)

    done = graph.invoke(Command(resume="2020"), _config(model, caller="analyst-8"))

    # Unchanged: this is still a crashed turn and still says why. The repair is about the thread.
    assert done["path_kind"] == "crashed"
    assert done["failure"] == {"stage": "agent_core", "error_type": "ResumeRejected"}
    assert not _dangling(done["messages"]), (
        f"unanswered tool_use {sorted(_dangling(done['messages']))} after a refused resume; the "
        "victim's thread would be unusable from here on"
    )
    assert not (done.get("clarifications") or []), "the refused answer must not be recorded"


# ── the pause, which must be left exactly as it is ───────────────────────────────────


def test_a_paused_turn_never_reaches_the_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    """The invariant stated as a structural fact rather than as a comment.

    ``_run`` re-raises ``GraphInterrupt`` untouched and ``agent_core_node`` calls it inside no
    ``try``, so a pause propagates out of the node and the write that seals stranded calls is
    never reached. This asserts that directly: a refactor that catches ``GraphInterrupt`` in the
    node would answer the ``ask_user`` call the paused turn is *waiting* on, and resume would
    break with nothing else in the suite objecting.
    """
    calls: list[Any] = []
    original = agent_core._sealed

    def spy(fresh: Any, failure: Any, **kwargs: Any) -> Any:
        calls.append((fresh, failure, kwargs))
        return original(fresh, failure, **kwargs)

    monkeypatch.setattr(agent_core, "_sealed", spy)
    graph = graph_module.compile_graph()
    paused = _paused(graph, ScriptedChatModel(responses=[_ask_user("c1"), AIMessage("ok: 2020")]))

    assert calls == [], f"a pause reached the repair: {calls!r}"
    # And nothing was committed at all, which is why there is nothing here to repair: the
    # pending ``tool_use`` lives in the *nested* agent's checkpoint until the resume.
    assert (paused.get("messages") or []) == []


def test_an_authorised_resume_still_gets_its_answer() -> None:
    """The paired positive, without which every assertion above is satisfiable by a graph that
    stopped resuming at all: the answer becomes the ``ask_user`` reply, and not an error one."""
    graph = graph_module.compile_graph()
    model = ScriptedChatModel(responses=[_ask_user("c1"), AIMessage("ok: 2020")])
    _paused(graph, model)

    done = graph.invoke(Command(resume="2020"), _config(model))

    assert done["path_kind"] == "answered", f"failure={done.get('failure')!r}"
    replies = [m for m in done["messages"] if str(getattr(m, "type", "")) == "tool"]
    assert [str(m.content) for m in replies] == ["2020"], (
        f"the human's answer is what closes the call, got {[str(m.content) for m in replies]!r}"
    )
    assert [getattr(m, "status", None) for m in replies] == ["success"]
    assert [c.get("answer") for c in done.get("clarifications") or []] == ["2020"]


def test_a_pause_returned_as_a_frame_is_left_alone() -> None:
    """The configuration where the pause does *not* raise, measured.

    With **no checkpointer** — the eval and CLI shape — the nested Pregel ends the stream instead
    of re-raising, and returns the pause in the frame as ``__interrupt__`` beside the pending
    ``tool_use``. So ``_sealed`` really is reached with a legitimate pause in hand, and the frame
    saying a pause is pending is what stops it. Nothing is lost by not sealing there: without a
    saver the state does not outlive the invocation, so no later turn replays it.
    """
    out = _turn(ScriptedChatModel(responses=[_ask_user("c1"), AIMessage("ok: 2020")]))

    replies = [m for m in out["messages"] if str(getattr(m, "type", "")) == "tool"]
    assert replies == [], (
        f"a pending clarification was answered by the repair: {[str(m.content) for m in replies]!r}"
    )


def test_a_clean_turn_is_left_exactly_as_it_was() -> None:
    """The guard against sealing turns that need no sealing — a spurious error reply is a
    message the model reads and reasons from."""
    out = _turn(ScriptedChatModel(responses=[AIMessage("three customers")]))

    assert out["path_kind"] == "answered"
    assert [getattr(m, "type", None) for m in out["messages"]] == ["ai"]
