"""The one-outstanding-question latch: only one ``ask_user`` may pause a turn at a time, and
answering it must give the latch back.

Split out of ``test_agent_tools_hitl.py`` (ADR 0005 §6 hard cap at 1,000 lines) rather than
appended there. Reuses that file's fixtures (``_tools``, ``_runtime``) bare -- ``tests/`` carries
no ``__init__.py``, so pytest's rootless import puts ``tests/serve/`` on ``sys.path`` and a
sibling module in the same directory is importable by its bare name, the same pattern
``test_ask_user_choices_and_defer.py`` already uses.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import Command
from test_agent_tools_hitl import _runtime, _tools

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.events import tool_event_id
from governed_bi.serve.graph import compile_graph
from governed_bi.serve.resume import CALLER_KEY, resume_clarification
from governed_bi.serve.scripted_model import ScriptedChatModel


def test_only_one_clarification_may_be_outstanding_per_turn() -> None:
    """A second ``ask_user`` in one assistant message is refused, not queued.

    **The bug it closes.** LangGraph dispatches one ``Send`` per pending tool call, so two
    ``ask_user`` calls in one message both interrupt. The surfacing order is a race,
    ``_clarification`` returns the first interrupt, and ``Command(resume=...)`` always lands on
    the first tool call — so the user is shown "which region?", answers it, and the answer is
    recorded against, and handed to the model as, "which year?". The resume surface carries no
    way to say *which* question is being answered, so the fix has to be that only one is ever
    outstanding.

    Both calls share one ``build_tools`` closure, which is what makes a latch work. The check
    and the set have no ``await`` between them, so two concurrent ``Send``s cannot both pass.
    """
    ask = _tools()["ask_user"]

    async def _both() -> tuple[Any, Any]:
        # `basis` is required in this fork; it routes the answer, not the pause.
        call = lambda q, c: ask.coroutine(  # noqa: E731
            question=q, runtime=_runtime(c), basis="data_definition"
        )
        first = asyncio.create_task(call("which region?", "c1"))
        second = asyncio.create_task(call("which year?", "c2"))
        done, pending = await asyncio.wait({first, second}, timeout=5)
        for task in pending:
            task.cancel()
        return done, {first: "first", second: "second"}

    done, _ = asyncio.run(_both())

    # Exactly one of the two returned. The other raised `GraphInterrupt` (it paused) or is still
    # pending; either way it did not produce a second question for the user to answer.
    replies = []
    for task in done:
        try:
            replies.append(task.result())
        except BaseException:  # noqa: BLE001 — GraphInterrupt is the paused call, not a failure
            pass
    assert len(replies) == 1, "exactly one ask_user should return a refusal without pausing"
    text = replies[0].update["messages"][0].content
    assert "one clarifying question" in text.lower()
    assert "already has one" in text.lower()


def _clarify_turn(*, thread: str, turn: str, token: str) -> dict[str, Any]:
    """A turn shaped for the ``ask_user`` path — the keys ``test_ask_user_interrupt_and_identity_resume`` uses."""
    return {
        "question": "revenue?", "thread_id": thread, "turn_index": 1, "turn_id": turn,
        "run_id": "r", "question_id": "q", "db_id": "sales", "attempt_id": "a",
        "corpus_content_hash": "c", "prompt_set_hash": "p", "knobs_resolved": {},
        "n_re_served": 0, "facet_route_hits": [("facet_schema", "sales", 1.0)],
        "messages": [], "usage": [], "identity": {"token": token}, "clarifications": [],
    }


def test_a_second_question_after_a_resume_is_not_refused_as_still_outstanding() -> None:
    """The one-outstanding-question latch has to be **given back** when the question is answered.

    ``pending_clarification`` is rebuilt by every ``build_tools`` — once per ``agent_core``
    execution — so it never accumulated across passes. It stayed *occupied*, which is a different
    bug and the one that reached the model. The call being resumed has no ``ToolMessage`` yet —
    that is exactly why its ``Send`` re-runs on the resume pass — so it re-appends its own
    ``clarification_id``, and nothing released it once ``interrupt()`` returned an answer.

    Measured before the fix, on this script: "which year?" is asked, answered "2020", and the
    model's next ``ask_user`` is refused with *"Only one clarifying question may be outstanding at
    a time, and this turn already has one"* — with the ``ToolMessage`` carrying "2020" sitting
    directly above the refusal in the transcript. That refusal also tells the model to ask "after
    this one is answered", which is what it had just done, so the advice is unfollowable.

    The assertion is that the second question **pauses** rather than returning a string: a real
    interrupt, on its own ``clarification_id``, answerable by the same identity.
    """
    graph = compile_graph()
    token = "identity-two-questions"
    config = {
        "configurable": {
            "thread_id": "t-two-q",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": ScriptedChatModel(
                responses=[
                    AIMessage(content="", tool_calls=[{
                        "name": "ask_user",
                        "args": {"question": "which year?", "basis": "data_definition"},
                        "id": "c1", "type": "tool_call",
                    }]),
                    AIMessage(content="", tool_calls=[{
                        "name": "ask_user",
                        "args": {"question": "which region?", "basis": "data_definition"},
                        "id": "c2", "type": "tool_call",
                    }]),
                    AIMessage(content="ok: 2020 EMEA"),
                ]
            ),
        }
    }

    paused = graph.invoke(_clarify_turn(thread="t-two-q", turn="turn-two-q", token=token), config)
    first = [i.value for i in (paused.get("__interrupt__") or ())]
    assert [v.get("question") for v in first] == ["which year?"], (
        f"precondition: the turn must pause on the first question, got {first}"
    )

    after = resume_clarification(graph, config=config, identity={"token": token}, answer="2020")

    texts = [str(getattr(m, "content", "")) for m in (after.get("messages") or ())]
    assert not [t for t in texts if "already has one" in t.lower()], (
        "the second question was refused as still outstanding, though the first was answered in "
        f"the same pass. transcript: {texts}"
    )
    second = [i.value for i in (after.get("__interrupt__") or ())]
    assert [v.get("question") for v in second] == ["which region?"], (
        f"the second question did not pause the turn; interrupts={second}, transcript={texts}"
    )
    assert second[0]["clarification_id"] != first[0]["clarification_id"], (
        "the two questions must not share a clarification_id, or a resume cannot say which one "
        "it answers"
    )

    done = resume_clarification(graph, config=config, identity={"token": token}, answer="EMEA")
    answers = [c.get("answer") for c in (done.get("clarifications") or ())]
    assert answers == ["2020", "EMEA"], (
        f"both answered clarifications must reach the record, got {answers}"
    )


def test_the_replayed_ask_user_start_reuses_the_row_id_rather_than_opening_a_second() -> None:
    """The resume replay re-emits ``start``. That is ADR 0010's contract, and this pins it.

    ``interrupt()`` cannot be reached without re-running everything above it, so the ``start``
    emitted before it is emitted again on the resume pass. Moving it after ``interrupt()`` would
    stop the repeat and delete the open row for the whole interval a human spends reading the
    question — the only interval it exists to describe — and would leave the ``refused`` resolve
    with no ``start`` on its own stream. ADR 0010 chose the repeat and paid for it with a stable
    ``id``: "the ``tools`` node re-executes on resume, so ``start`` is emitted twice, and a
    seq-derived id would have shown the same step twice".

    So the property is not "one start" but **"one row"**: every event for one ``ask_user`` call
    shares ``tool_event_id("ask_user", tool_call_id)`` and the resolve arrives last, so
    ``ui/lib/steps.ts::reduceSteps`` — which merges on ``id`` — folds all three into a single row
    that settles resolved. A repeat carrying a fresh id would show the user their clarification
    twice; a resolve arriving before a replayed ``start`` would show it spinning after it was
    answered.

    Measured through the real graph rather than reasoned: the emitter is not what decides how many
    times the node re-executes.
    """
    graph = compile_graph()
    token = "identity-replay"
    conf = {
        "thread_id": "t-replay",
        "policy": GovernancePolicy(guard_rules_enabled={}),
        "agent_model": ScriptedChatModel(
            responses=[
                AIMessage(content="", tool_calls=[{"name": "ask_user",
                                                   "args": {"question": "which year?", "basis": "data_definition"},
                                                   "id": "c1", "type": "tool_call"}]),
                AIMessage(content="ok: 2020"),
            ]
        ),
    }

    def ask_user_events(payload: Any, configurable: dict[str, Any]) -> list[dict[str, Any]]:
        # ``subgraphs=True`` is not optional: the tools run inside the nested ``create_agent``
        # graph, and LangGraph does not propagate a nested writer to the parent stream without it.
        out: list[dict[str, Any]] = []
        for chunk in graph.stream(
            payload, {"configurable": configurable}, stream_mode="custom", subgraphs=True
        ):
            while isinstance(chunk, tuple) and chunk:
                chunk = chunk[-1]
            if isinstance(chunk, dict) and chunk.get("step") == "ask_user":
                out.append(chunk)
        return out

    paused = ask_user_events(
        _clarify_turn(thread="t-replay", turn="turn-replay", token=token), conf
    )
    resumed = ask_user_events(Command(resume="2020"), {**conf, CALLER_KEY: token})
    events = paused + resumed

    assert [e["status"] for e in events] == ["start", "start", "ok"], (
        "the ask_user row is start (pause), start (replay), then one resolve; got "
        f"{[(e['status'], e['seq']) for e in events]}"
    )
    assert {e["id"] for e in events} == {tool_event_id("ask_user", "c1")}, (
        f"the replayed start opened a second row: {sorted({e['id'] for e in events})}"
    )
    assert len({e["detail"]["clarification_id"] for e in events}) == 1, (
        "the replay must re-derive the same clarification_id, or the client's side table cannot "
        f"join onto the row: {[e['detail'] for e in events]}"
    )
    assert events[-1]["status"] != "start", "the resolve must arrive last, or the row spins"
