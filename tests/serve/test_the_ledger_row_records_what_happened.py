"""The clarifications ledger must say what became of the question it opened.

``ask_user`` writes an ``open`` row before ``interrupt`` pauses the turn, so the question
survives an abandoned turn nobody ever resumes (``serve/tools.py::_log_live_clarification``).
Nothing then closed it. Every live clarification stayed ``open`` for the rest of the corpus's
life, whatever the user actually did, which made the admin's queue report three different
situations as one:

  - nobody has looked at this question yet,
  - the user answered it in chat, and
  - the user pressed "I don't know -- ask the admin later".

Live verification of ``ryan/merge-upstream-0814`` accumulated five indistinguishable ``open``
rows across a session where some had been answered and some deferred. The third case is the
one the ledger exists to carry -- a defer *is* the hand-off to the admin -- and it was the one
it could not express, because ``ClarificationRecordStatus`` had no ``deferred`` member.

**Scope of the fix these tests pin.** The resume path now closes the row it opened, with
``answered`` or ``deferred``. It does not add a ``declined`` status: no shipped surface can
send one (``components/chat/clarification-prompt.tsx`` has a defer button and no decline
button), and a declined question is genuinely still open homework, so ``open`` states that
correctly. If a decline button ever ships, this is the seam it lands on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from governed_bi.curator.clarifications import (
    ClarificationRecordStatus,
    load_clarifications,
)
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.graph import compile_graph
from governed_bi.serve.resume import resume_clarification
from governed_bi.serve.scripted_model import ScriptedChatModel

QUESTION = "which year?"


def _model() -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_user",
                        "args": {"question": QUESTION, "basis": "data_definition"},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Assuming 2024 (unconfirmed), revenue is $18,496."),
        ]
    )


def _paused(corpus_root: Path, thread: str) -> tuple[Any, dict[str, Any]]:
    """Drive one ``ask_user`` call to its interrupt over a real ``corpus_root``.

    ``corpus_root`` is what makes these tests different from
    ``test_ask_user_choices_and_defer.py``'s: that file asserts on the ``clarifications``
    *state channel*, which needs no corpus at all. The ledger is a file, so it needs one.
    """
    graph = compile_graph()
    config: dict[str, Any] = {
        "configurable": {
            "thread_id": thread,
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": _model(),
            "corpus_root": corpus_root,
        }
    }
    turn = {
        "question": "revenue?",
        "thread_id": thread,
        "turn_index": 1,
        "turn_id": f"turn-{thread}",
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
        "identity": {"token": thread},
        "clarifications": [],
    }
    paused = graph.invoke(turn, config)
    assert paused.get("__interrupt__"), "precondition: ask_user paused the turn"
    return graph, config


def _only_row(corpus_root: Path) -> Any:
    (row,) = load_clarifications(corpus_root)
    return row


def test_an_abandoned_clarification_stays_open(tmp_path: Path) -> None:
    """The behaviour worth keeping: a turn paused and never resumed leaves an ``open`` row.

    This is the whole point of logging before ``interrupt``, so it is asserted here rather
    than assumed -- the fix below must not close a row on the strength of the interrupt alone.
    """
    _paused(tmp_path, "t-ledger-abandoned")

    row = _only_row(tmp_path)
    assert row.status is ClarificationRecordStatus.open
    assert row.question == QUESTION
    assert row.source == "live_chat"
    assert row.answer is None


def test_a_deferred_clarification_is_closed_as_deferred(tmp_path: Path) -> None:
    """The defect: pressing defer must be distinguishable from nobody having looked.

    ``answer`` stays ``None`` on the row deliberately. The state channel's ``answer`` for a
    defer is ``_CLARIFY_DEFERRED_TEXT`` -- an instruction to the *model* to proceed on its own
    judgment -- and writing that into the ledger's ``answer`` field would show an admin a
    sentence the user never said, in the column where the user's own words go.
    """
    graph, config = _paused(tmp_path, "t-ledger-defer")

    resume_clarification(
        graph, config=config, identity={"token": "t-ledger-defer"}, answer={"defer": True}
    )

    row = _only_row(tmp_path)
    assert row.status is ClarificationRecordStatus.deferred
    assert row.answer is None
    assert row.answered_by is None


def test_a_live_answer_closes_its_own_row(tmp_path: Path) -> None:
    """Same root cause, commoner case: an answered question must leave the open queue.

    ``curator/scan_report.py`` counts ``status is open`` as unresolved, and the admin's
    Clarifications tab lists the same rows. A live answer that never closed its row is
    permanent homework nobody can discharge -- answering it again offline is the only way to
    clear it, which re-asks the user a question they have already answered.
    """
    graph, config = _paused(tmp_path, "t-ledger-answered")

    resume_clarification(
        graph, config=config, identity={"token": "t-ledger-answered"}, answer={"answer": "2024"}
    )

    row = _only_row(tmp_path)
    assert row.status is ClarificationRecordStatus.answered
    assert row.answer == "2024"
    assert row.answered_by == "user"
    # `converted_to_corpus` is the *ledger* fold's idempotency marker
    # (`curator/clarification.py::fold_ledger_answer_into_corpus`, offline only). A live turn
    # folds through `fold_answered_clarification`, which never touches the ledger, so `False`
    # here is accurate rather than stale -- and nothing polls the field, so it strands nothing.
    assert row.converted_to_corpus is False


def test_a_choice_answer_records_the_choice_id(tmp_path: Path) -> None:
    """A grounded multiple-choice answer must land its ``choice_id``, not just resolved text.

    ``ask_user``'s ``choices`` argument exists so the UI can offer the corpus's own spellings;
    an admin auditing the ledger later needs to see *which* option was picked, and the resolved
    label alone cannot be matched back to a choice whose label has since been re-worded.
    """
    graph, config = _paused(tmp_path, "t-ledger-choice")

    resume_clarification(
        graph,
        config=config,
        identity={"token": "t-ledger-choice"},
        answer={"choice_id": "fiscal_2024"},
    )

    row = _only_row(tmp_path)
    assert row.status is ClarificationRecordStatus.answered
    assert row.answer_choice_id == "fiscal_2024"
