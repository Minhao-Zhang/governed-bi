"""``ask_user``'s ``choices``/``allow_freeform`` and the ``defer``/``declined`` alias.

Split out of ``test_agent_tools_hitl.py`` (ADR 0005 §6 hard cap at 1,000 lines) rather than
appended there. Reuses that file's fixtures (``_tools``, ``_call``) bare -- ``tests/`` carries
no ``__init__.py``, so pytest's rootless import puts ``tests/serve/`` on ``sys.path`` and a
sibling module in the same directory is importable by its bare name, the same pattern
``test_stream_events_end_to_end.py`` already uses for ``turn_contract_fixtures``.

Three things this initiative changed on ``ask_user``, covered here:

1. Feature gap 2 -- ``choices``/``allow_freeform`` are new optional arguments, matching
   ``governed-bi-ui``'s already-built ``clarificationChoiceSchema`` wire contract, which v2's
   ``ask_user`` never populated.
2. The Phase 1 reversal -- ``basis`` was deliberately withheld from the ``interrupt()`` payload
   as "a backend-only routing signal"; it is added back here because the UI now needs it to hide
   the defer-to-admin button for ``ranking_ambiguity`` questions.
3. Bug 3 -- governed-bi-ui's "I don't know -- ask the admin later" button sends
   ``{"defer": True}``, a payload shape ``_clarification_answer`` and ``ask_user``'s own
   ``declined`` computation used to silently fail to recognise.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.graph import compile_graph
from governed_bi.serve.resume import resume_clarification
from governed_bi.serve.scripted_model import ScriptedChatModel
from test_agent_tools_hitl import _call, _tools


def test_ask_user_choices_and_allow_freeform_are_optional_in_the_schema() -> None:
    """Feature gap 2: the UI's ``clarificationChoiceSchema`` contract already supports grounded
    multiple-choice, but v2's ``ask_user`` never populated it. Both new arguments must be
    optional -- a call that never mentions them (the common case: nothing grounded) must not be
    rejected as missing a required field.
    """
    tools = _tools()
    required = tools["ask_user"].tool_call_schema.model_json_schema().get("required", [])
    schema = tools["ask_user"].args
    assert "choices" in schema
    assert "allow_freeform" in schema
    assert "choices" not in required
    assert "allow_freeform" not in required


def test_ask_user_rejects_a_malformed_choice_before_pausing() -> None:
    """A choice missing ``id`` or ``label`` must be rejected the same idiom as a schema-term
    leak (``find_schema_leak``) -- reply text telling the model to retry, never a raised
    exception that would kill the turn on a live interrupt-bearing call.
    """
    tools = _tools()
    text, update = _call(
        tools["ask_user"],
        question="Which region counts as headquarters?",
        basis="data_definition",
        choices=[{"id": "east", "label": "East"}, {"id": "", "label": "West"}],
    )
    assert "rejected" in text
    assert "clarifications_by_call" not in update


def test_ask_user_interrupt_payload_carries_basis_choices_and_allow_freeform() -> None:
    """Phase 6 reversal (this initiative): ``basis`` was deliberately withheld from the
    ``interrupt()`` payload in Phase 1 as "a backend-only routing signal" -- the UI now needs it
    to hide the defer-to-admin button for ``ranking_ambiguity`` questions, a real behavioural
    need Phase 1 did not have. ``choices``/``allow_freeform`` must reach the same payload
    verbatim when the model grounds candidates.
    """
    grounded_choices = [
        {"id": "north", "label": "North region"},
        {"id": "south", "label": "South region"},
    ]
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_user",
                        "args": {
                            "question": "Which region do you mean?",
                            "basis": "data_definition",
                            "choices": grounded_choices,
                            "allow_freeform": True,
                        },
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="ok: north"),
        ]
    )
    graph = compile_graph()
    config = {
        "configurable": {
            "thread_id": "t-hitl-choices",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
        }
    }
    turn = {
        "question": "revenue?",
        "thread_id": "t-hitl-choices",
        "turn_index": 1,
        "turn_id": "turn-hitl-choices",
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
        "identity": {"token": "identity-choices"},
        "clarifications": [],
    }
    paused = graph.invoke(turn, config)
    interrupts = paused.get("__interrupt__")
    assert interrupts, "precondition: ask_user paused the turn"
    payload = interrupts[0].value
    assert payload["basis"] == "data_definition"
    assert payload["choices"] == grounded_choices
    assert payload["allow_freeform"] is True


def test_clarification_answer_treats_defer_as_declined() -> None:
    """Bug 3: governed-bi-ui's "I don't know -- ask the admin later" button sends
    ``{"clarification_id": ..., "defer": True}`` -- a distinct variant from
    ``{"declined": True}`` in ``lib/types.ts``'s ``ClarificationResponse`` union.
    ``_clarification_answer`` used to check only ``declined``, so a ``defer`` payload matched
    neither ``declined`` nor ``answer``/``choice_id``/``text`` and silently fell through to
    ``""`` -- an empty-string "answer" indistinguishable from the user genuinely answering
    nothing. Both must produce the identical declined-sentinel text.
    """
    from governed_bi.serve.tools import _clarification_answer

    declined_text = _clarification_answer({"declined": True})
    defer_text = _clarification_answer({"defer": True})
    assert defer_text == declined_text == "The user declined to answer this clarification."


def test_ask_user_resume_with_defer_is_recorded_as_declined() -> None:
    """End to end: resuming a paused ``ask_user`` with ``{"defer": True}`` must record
    ``declined: True`` on the clarification, exactly as ``{"declined": True}`` already does --
    not an answered turn carrying an empty-string answer.
    """
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_user",
                        "args": {"question": "which year?", "basis": "data_definition"},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="ok"),
        ]
    )
    graph = compile_graph()
    token = "identity-defer"
    config = {
        "configurable": {
            "thread_id": "t-hitl-defer",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
        }
    }
    turn = {
        "question": "revenue?",
        "thread_id": "t-hitl-defer",
        "turn_index": 1,
        "turn_id": "turn-hitl-defer",
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
        "identity": {"token": token},
        "clarifications": [],
    }
    paused = graph.invoke(turn, config)
    assert paused.get("__interrupt__"), "precondition: ask_user paused the turn"

    done = resume_clarification(
        graph, config=config, identity={"token": token}, answer={"defer": True}
    )
    clars = done.get("clarifications") or []
    assert clars and clars[0]["declined"] is True, clars
    assert clars[0]["answer"] == "The user declined to answer this clarification."
