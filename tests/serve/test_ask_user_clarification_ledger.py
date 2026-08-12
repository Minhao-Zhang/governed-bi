"""``ask_user`` writes an open ledger record before ``interrupt()`` pauses (Phase 1b, UtkuAI).

Split out from ``test_agent_tools_hitl.py`` (ADR 0005 §6 file-length cap), same pattern as
``test_ask_user_choices_and_defer.py``: a real ``compile_graph()`` + ``ScriptedChatModel``
interrupt/resume round trip, not a direct tool-body call (``_call``) -- the property under
test is that the record survives regardless of whether the turn is ever resumed, which only a
real ``interrupt()`` pause can exercise.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage

from governed_bi.curator.clarifications import load_clarifications
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.graph import compile_graph
from governed_bi.serve.resume import resume_clarification
from governed_bi.serve.scripted_model import ScriptedChatModel


def _turn(**overrides: object) -> dict[str, object]:
    turn: dict[str, object] = {
        "question": "revenue?",
        "thread_id": "t-ledger-hitl",
        "turn_index": 1,
        "turn_id": "turn-ledger-hitl",
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
    turn.update(overrides)
    return turn


def _model() -> ScriptedChatModel:
    return ScriptedChatModel(
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
            AIMessage(content="ok: 2020"),
        ]
    )


def test_ask_user_logs_an_open_ledger_record_that_survives_an_abandoned_turn(
    tmp_path: Path,
) -> None:
    """The whole point of Phase 1b's ledger write: even a turn nobody ever resumes leaves the
    question as admin homework. Asserted *before* any resume call -- nothing here ever resumes
    this thread.
    """
    graph = compile_graph()
    config = {
        "configurable": {
            "thread_id": "t-ledger-abandoned",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": _model(),
            "corpus_root": tmp_path,
        }
    }
    paused = graph.invoke(
        _turn(thread_id="t-ledger-abandoned", turn_id="turn-abandoned"), config
    )
    assert paused.get("__interrupt__"), "precondition: ask_user paused the turn"

    records = load_clarifications(tmp_path)
    assert len(records) == 1, records
    record = records[0]
    assert record.status.value == "open"
    assert record.source == "live_chat"
    assert record.scope == f"live_chat:{record.id}"
    assert record.question == "which year?"


def test_ask_user_skips_the_ledger_write_cleanly_with_no_corpus_root() -> None:
    """No ``corpus_root`` on this session (eval/offline callers) -- must pause the turn exactly
    as before, with no error and nothing written anywhere.
    """
    graph = compile_graph()
    config = {
        "configurable": {
            "thread_id": "t-ledger-no-root",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": _model(),
        }
    }
    paused = graph.invoke(
        _turn(thread_id="t-ledger-no-root", turn_id="turn-no-root"), config
    )
    assert paused.get("__interrupt__"), "precondition: ask_user paused the turn"


def test_ask_user_ledger_write_is_idempotent_across_a_resume(tmp_path: Path) -> None:
    """``interrupt()`` re-runs ``ask_user`` from the top on resume -- the ledger write must not
    duplicate the record it already wrote before the first pause (matches v1's
    ``test_ask_user_answer_updates_same_record_not_a_duplicate``).
    """
    graph = compile_graph()
    token = "identity-ledger-idem"
    config = {
        "configurable": {
            "thread_id": "t-ledger-idem",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": _model(),
            "corpus_root": tmp_path,
        }
    }
    turn = _turn(
        thread_id="t-ledger-idem", turn_id="turn-idem", identity={"token": token}
    )
    paused = graph.invoke(turn, config)
    assert paused.get("__interrupt__"), "precondition: ask_user paused the turn"
    assert len(load_clarifications(tmp_path)) == 1

    resume_clarification(graph, config=config, identity={"token": token}, answer="2020")

    records = load_clarifications(tmp_path)
    assert len(records) == 1, "the resume must not have written a second record"
    assert records[0].status.value == "open", (
        "Phase 1b only writes the open record before interrupt(); folding a real answer into "
        "the ledger (status -> answered) is a later phase, not this one"
    )
