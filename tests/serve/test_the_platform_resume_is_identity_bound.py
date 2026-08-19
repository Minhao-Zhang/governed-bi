"""ADR 0006 B9 on the transport that is about to be the only one.

``POST /chat/resume`` is being retired, and it was the sole caller of
``serve/resume.resume_clarification`` — the only thing that ever compared the caller answering a
clarification against the caller who was asked. LangGraph Server resumes by posting a run with
``{"command": {"resume": …}}``, which the runtime applies to the pending interrupt *inside* the
graph; nothing in ``api/`` is consulted. So the route's deletion would have deleted a governance
control, silently, with every existing test still green.

**These tests drive the platform's shape rather than a route.** What LangGraph Server does to
resume is exactly ``graph.invoke(Command(resume=…), config)`` with the authenticated caller in
``config["configurable"]["langgraph_auth_user_id"]`` (``langgraph_api/models/run.py`` writes that
key from the auth context, after the client's own config is merged, and
``validation.py::RESERVED_CONFIGURABLE_KEYS`` refuses a request that names it). Reproducing that
here needs no server, no port and no model provider — and it is the same call the gate sees in
production, which is the property ``tests/api/test_a_run_cannot_write_state.py`` was rewritten to
have after a handler-level test passed against a dead handler for weeks.

The positive case is not decoration. Every negative assertion below is "the answer did not
arrive", and a gate that refused *everything* — or a graph that quietly stopped resuming at all —
would satisfy all of them. The paired authorised resume is what makes the negatives mean
something.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.graph import compile_graph
from governed_bi.serve.resume import CALLER_KEY, ResumeRejected, authorise_resume, caller_identity
from governed_bi.serve.scripted_model import ScriptedChatModel

ASKED = "analyst-7"
ANSWER = "the 2020 fiscal year"


def _model() -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_user",
                        # `basis` is required in this fork: the tool routes the answer by it, so
                        # a call without it never reaches the interrupt (see
                        # `tests/api/test_http_contract.py::
                        # test_a_clarification_interrupt_carries_an_id_and_a_reason`).
                        "args": {"question": "which year?", "basis": "data_definition"},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="ok: 2020"),
        ]
    )


def _config(thread_id: str, caller: str | None) -> dict[str, Any]:
    conf: dict[str, Any] = {
        "thread_id": thread_id,
        "policy": GovernancePolicy(guard_rules_enabled={}),
        "agent_model": _model(),
    }
    if caller is not None:
        conf[CALLER_KEY] = caller
    return {"configurable": conf}


def _turn(thread_id: str, identity: Any) -> dict[str, Any]:
    turn: dict[str, Any] = {
        "question": "revenue?",
        "thread_id": thread_id,
        "turn_index": 1,
        "turn_id": f"turn-{thread_id}",
        "run_id": "r",
        "question_id": "q",
        "db_id": "sales",
        "attempt_id": "a",
        "corpus_content_hash": "c",
        "prompt_set_hash": "p",
        "knobs_resolved": {},
        "n_re_served": 0,
        "facet_route_hits": [("facet_schema", "sales", 1.0)],
        "messages": [],
        "usage": [],
        "clarifications": [],
    }
    if identity is not None:
        turn["identity"] = identity
    return turn


def _paused(thread_id: str, *, stored: Any) -> Any:
    """A turn paused on ``ask_user``, exactly as the streamed path leaves one."""
    graph = compile_graph()
    paused = graph.invoke(_turn(thread_id, stored), _config(thread_id, caller=ASKED))
    assert paused.get("__interrupt__"), "precondition: ask_user did not pause the turn"
    return graph


def _answers(result: Any) -> list[str]:
    return [str(c.get("answer") or "") for c in (result.get("clarifications") or [])]


def test_a_resume_by_another_caller_does_not_apply_the_answer() -> None:
    """The property, on the call LangGraph Server makes.

    Two different requests: the paused turn was checkpointed for ``analyst-7``, the resume is
    posted by ``analyst-8``. The answer must not become a ``ToolMessage``, must not be handed to
    the model, and must not reach ``clarifications`` — that channel is what the turn record and
    ``/audit/turns`` project, so an applied resume is durable.
    """
    graph = _paused("t-hijack", stored={"token": ASKED})

    done = graph.invoke(
        Command(resume=ANSWER), _config("t-hijack", caller="analyst-8")
    )

    assert ANSWER not in _answers(done), (
        "an unauthorised resume was applied: the clarification the wrong caller answered is in "
        f"the record as {_answers(done)!r}"
    )
    assert "ResumeRejected" in repr(done.get("failure")), (
        "the turn did not fail on the identity gate, so something else stopped the answer and "
        f"this test is not measuring the gate. failure={done.get('failure')!r}"
    )


def test_the_caller_that_was_asked_resumes_normally() -> None:
    """The paired positive: a gate that refuses everyone is not the control.

    Same graph, same thread shape, same ``Command(resume=…)`` — only ``langgraph_auth_user_id``
    differs from the test above.
    """
    graph = _paused("t-ok", stored={"token": ASKED})

    done = graph.invoke(Command(resume=ANSWER), _config("t-ok", caller=ASKED))

    assert ANSWER in _answers(done), (
        f"the authorised caller's answer never reached the record: {_answers(done)!r}"
    )
    # `no_sql` is in the set because the scripted model closes in prose without calling
    # `run_query`, so the resumed turn ran no governed statement (it read `answered` until
    # 2026-08-18). The gate under test is the identity one: what must not appear is a refusal or
    # a crash.
    assert done["answer"]["outcome"] in {"answered", "clarification", "no_sql"}, (
        done["answer"]["outcome"]
    )


def test_a_turn_that_recorded_no_identity_refuses_every_resume() -> None:
    """G1 — absence refuses. ``resume_authorised`` refuses two ``None``s deliberately.

    This is the fail-closed half of the ``serve/accept.py`` wiring: if that node ever stops
    storing the authenticated caller, the streamed path does not get an *ungated* resume, it gets
    no resume at all. A wiring break that presents as a broken feature is one somebody fixes; one
    that presents as a working feature with the check gone is audit A5.
    """
    graph = _paused("t-anon", stored=None)

    done = graph.invoke(Command(resume=ANSWER), _config("t-anon", caller=ASKED))

    assert ANSWER not in _answers(done), _answers(done)
    assert "ResumeRejected" in repr(done.get("failure")), done.get("failure")


def test_the_gate_reads_only_the_slot_a_client_cannot_write() -> None:
    """A second, client-writable spelling of "who is calling" would delete the gate.

    ``langgraph_auth_user_id`` is reserved: ``langgraph_api/validation.py`` rejects a run,
    assistant or cron write that names it, and ``POST /threads`` has no ``config`` field to
    inherit one from. Nothing else in ``configurable`` has that protection, so a tolerant reader
    that also accepted ``identity`` or ``caller_identity`` would let the attacker name the victim
    and pass — which is worse than no gate, because the record would then say the right person
    answered.
    """
    forged = {
        "configurable": {
            "identity": ASKED,
            "caller_identity": ASKED,
            "user_id": ASKED,
            "langgraph_auth_user": {"identity": ASKED},
        }
    }
    assert caller_identity(forged) is None, (
        "a client-writable configurable key named a caller; the gate is bypassable by putting the "
        "victim's identity in the request"
    )
    with pytest.raises(ResumeRejected):
        authorise_resume({"identity": {"token": ASKED}}, forged)


def test_the_accept_node_stores_the_transport_authenticated_caller() -> None:
    """The write half — without it the gate above refuses every streamed clarification.

    ``accept`` is the only node the streamed transport enters through, and it used to pass no
    ``identity`` at all (audit A5). It takes the caller from ``configurable``, never from the
    conversation: ``ServeInput`` declares one key, so ``messages`` is all a client can write.
    """
    from governed_bi.serve.accept import accept_node
    from governed_bi.serve.session import Session

    session = Session(
        index=None, structure=None, assets_by_id={}, corpus=None, connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}), corpus_content_hash="c",
        prompt_set_hash="p", knobs_resolved={}, db_id="d", run_id="r",
    )
    accept = accept_node(session)
    message = [{"type": "human", "content": "revenue?"}]

    named = accept(
        {"messages": message},
        {"configurable": {"thread_id": "t-1", CALLER_KEY: ASKED}},
    )
    assert named.get("identity") == {"token": ASKED}, named.get("identity")

    anonymous = accept({"messages": message}, {"configurable": {"thread_id": "t-1"}})
    assert "identity" not in anonymous, (
        "a null identity was stored where none was authenticated; `Session.turn` omits the key so "
        f"that absence keeps failing closed, but this node wrote {anonymous.get('identity')!r}"
    )
