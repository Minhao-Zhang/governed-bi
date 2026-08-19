"""GET /threads/{thread_id}/raised (utku-ai-trust-loop-plan.md, task B-1) -- given a thread,
what did it raise, and what became of it. Reads both ``feedback.jsonl`` (task H) and the
refusal-sourced slice of ``clarifications.jsonl`` (task A), correlated to a thread through the
turn log (``api/thread_turns.ThreadTurnLog`` since ADR 0014; ``api/trace_store.py`` is deleted).

Session fixture shape mirrors ``tests/api/test_feedback_routes.py``/``test_drafts_route.py``.
``_turn_log`` returns an in-memory double rather than a real ``ThreadTurnLog`` (which reads
thread state through the Agent server's in-process transport and cannot resolve outside it,
per that class's own ``InProcessServerRequired``) -- this route's whole dependency on it is
``get_turn``/``list_turns`` (see ``trust_loop_routes.make_raised_router``'s docstring), so a
double needs only those two reader methods, seeded directly rather than through a file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")

_DB_ID = "beer"


def _session_with_corpus_root(tmp_path: Path) -> Any:
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.retrieve.structure import CorpusStructure
    from governed_bi.serve.session import Session

    structure = CorpusStructure(
        join_edges=frozenset(), references={}, asset_types={}, table_schemas={},
        schema_tags={}, joins_by_edge={},
    )
    return Session(
        index=None, structure=structure, assets_by_id={}, corpus=None, connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}), corpus_content_hash="c",
        prompt_set_hash="p", knobs_resolved={}, db_id=_DB_ID, run_id="r",
        corpus_root=tmp_path,
    )


def _session_without_corpus_root() -> Any:
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.retrieve.structure import CorpusStructure
    from governed_bi.serve.session import Session

    structure = CorpusStructure(
        join_edges=frozenset(), references={}, asset_types={}, table_schemas={},
        schema_tags={}, joins_by_edge={},
    )
    return Session(
        index=None, structure=structure, assets_by_id={}, corpus=None, connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}), corpus_content_hash="c",
        prompt_set_hash="p", knobs_resolved={}, db_id=_DB_ID, run_id="r",
        corpus_root=None,
    )


class _FakeTurnLog:
    """In-memory stand-in for ``api.thread_turns.ThreadTurnLog``, seeded directly. Provides only
    the two reader methods this route calls (``get_turn``/``list_turns``) plus ``append_turn``
    for this file's own seeding helper -- see the module docstring."""

    TURN_LOG_DIR = Path("/nowhere")
    SUMMARY_FIELDS: tuple[str, ...] = ("turn_id", "outcome")

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def append_turn(self, record: dict[str, Any], *, question: str, answer_text: str) -> None:
        self.rows.append(
            {"record": record, "question": question, "answer_text": answer_text,
             "outcome": record.get("outcome")}
        )

    def list_turns(self, limit: int = 50, thread_id: str | None = None) -> list[dict[str, Any]]:
        from governed_bi.api.thread_turns import summarise_turn

        rows = [r for r in self.rows if thread_id is None or r["record"].get("thread_id") == thread_id]
        return [summarise_turn(r) for r in rows[:limit]]

    def get_turn(self, turn_id: str) -> dict[str, Any] | None:
        return next((r for r in self.rows if r["record"].get("turn_id") == turn_id), None)


def _turn_log(monkeypatch, tmp_path: Path) -> Any:
    """A fresh :class:`_FakeTurnLog`. Kept as a function (over constructing one inline) so every
    test reads the same as it did against the old ``trace_store`` fixture."""
    return _FakeTurnLog()


def _client(trace_store: Any, session: Any):
    from fastapi.testclient import TestClient

    from governed_bi.api import routes

    return TestClient(routes.make_app(session, trace_store))


def _log_turn(trace_store: Any, turn_id: str, thread_id: str, **extra: Any) -> None:
    trace_store.append_turn(
        {"turn_id": turn_id, "thread_id": thread_id, **extra},
        question="does not matter for this route",
        answer_text="does not matter for this route",
    )


def test_no_corpus_root_is_an_empty_list(monkeypatch, tmp_path: Path) -> None:
    trace_store = _turn_log(monkeypatch, tmp_path)
    client = _client(trace_store, _session_without_corpus_root())
    response = client.get("/threads/th-1/raised")
    assert response.status_code == 200
    assert response.json() == []


def test_an_unknown_thread_is_an_empty_list(monkeypatch, tmp_path: Path) -> None:
    trace_store = _turn_log(monkeypatch, tmp_path)
    client = _client(trace_store, _session_with_corpus_root(tmp_path))
    assert client.get("/threads/th-nobody-asked-anything/raised").json() == []


# ── feedback (task H) ────────────────────────────────────────────────────────────────────────


def test_a_report_on_this_thread_is_reported_uncertified_before_any_admin_action(
    monkeypatch, tmp_path: Path
) -> None:
    from governed_bi.curator.feedback import FeedbackRecord, write_feedback

    trace_store = _turn_log(monkeypatch, tmp_path)
    _log_turn(trace_store, "t1", "th-1")
    write_feedback(
        tmp_path,
        [FeedbackRecord(id="f1", turn_id="t1", question="What counts as active?", answer_text="wrong")],
    )
    client = _client(trace_store, _session_with_corpus_root(tmp_path))

    (row,) = client.get("/threads/th-1/raised").json()
    assert row == {
        "kind": "feedback",
        "id": "f1",
        "question": "What counts as active?",
        "status": "open",
        "raised_at": None,
        "certified": False,
    }


def test_a_report_becomes_certified_once_its_draft_is_approved(monkeypatch, tmp_path: Path) -> None:
    """Answers the report through the real route (task H's own fold path -- no existing
    certified assets in a fresh `tmp_path`, so the Enhancer's model call is never reached, per
    `curator/enhancer.py::decide_fold`'s own early return), then approves the resulting draft
    and confirms this route sees it without a process restart (`_reload_assets`)."""
    from governed_bi.corpus.drafts import approve_draft
    from governed_bi.corpus.store import load
    from governed_bi.curator.feedback import FeedbackRecord, write_feedback

    trace_store = _turn_log(monkeypatch, tmp_path)
    _log_turn(trace_store, "t1", "th-1")
    write_feedback(
        tmp_path,
        [FeedbackRecord(id="f1", turn_id="t1", question="What counts as active?", answer_text="wrong")],
    )
    client = _client(trace_store, _session_with_corpus_root(tmp_path))

    answer = client.post("/feedback/f1/answer", json={"correction": "90 days"})
    assert answer.status_code == 200, answer.text

    before = client.get("/threads/th-1/raised").json()
    assert before[0]["certified"] is False

    assets, _problems = load(tmp_path)
    (draft,) = assets
    approve_draft(tmp_path, draft.id)

    after = client.get("/threads/th-1/raised").json()
    assert after[0]["certified"] is True
    assert after[0]["status"] == "answered"


def test_a_report_on_a_different_thread_is_excluded(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.feedback import FeedbackRecord, write_feedback

    trace_store = _turn_log(monkeypatch, tmp_path)
    _log_turn(trace_store, "t1", "th-other")
    write_feedback(
        tmp_path,
        [FeedbackRecord(id="f1", turn_id="t1", question="q?", answer_text="a.")],
    )
    client = _client(trace_store, _session_with_corpus_root(tmp_path))
    assert client.get("/threads/th-1/raised").json() == []


def test_a_report_whose_turn_is_not_in_the_log_is_excluded(monkeypatch, tmp_path: Path) -> None:
    """No way to tell "raised on a different thread" from "the turn log does not have this turn"
    -- both fail closed, per the module's own documented choice."""
    from governed_bi.curator.feedback import FeedbackRecord, write_feedback

    trace_store = _turn_log(monkeypatch, tmp_path)
    write_feedback(
        tmp_path,
        [FeedbackRecord(id="f1", turn_id="t-never-logged", question="q?", answer_text="a.")],
    )
    client = _client(trace_store, _session_with_corpus_root(tmp_path))
    assert client.get("/threads/th-1/raised").json() == []


def test_a_dismissed_report_is_reported_with_its_real_status_and_uncertified(
    monkeypatch, tmp_path: Path
) -> None:
    """The read model tells the whole truth even though the reader-facing surface built on it
    (task B-2) chooses not to render this state -- see the module docstring."""
    from governed_bi.curator.feedback import FeedbackRecord, FeedbackRecordStatus, write_feedback

    trace_store = _turn_log(monkeypatch, tmp_path)
    _log_turn(trace_store, "t1", "th-1")
    write_feedback(
        tmp_path,
        [
            FeedbackRecord(
                id="f1", turn_id="t1", question="q?", answer_text="a.",
                status=FeedbackRecordStatus.dismissed,
            )
        ],
    )
    client = _client(trace_store, _session_with_corpus_root(tmp_path))

    (row,) = client.get("/threads/th-1/raised").json()
    assert row["status"] == "dismissed"
    assert row["certified"] is False


# ── refusal-clarifications (task A, traced via B-0) ──────────────────────────────────────────


def test_a_refusal_clarification_with_a_turn_id_is_reported(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        ClarificationRecordStatus,
        write_clarifications,
    )

    trace_store = _turn_log(monkeypatch, tmp_path)
    _log_turn(trace_store, "t1", "th-1")
    write_clarifications(
        tmp_path,
        [
            ClarificationRecord(
                id="refusal-1", scope="refusal:1", question="What does popular mean?",
                status=ClarificationRecordStatus.answered, answer="Highest downloads.",
                answered_by="user", source="refusal", basis="data_definition", turn_id="t1",
            )
        ],
    )
    client = _client(trace_store, _session_with_corpus_root(tmp_path))

    (row,) = client.get("/threads/th-1/raised").json()
    assert row["kind"] == "clarification"
    assert row["id"] == "refusal-1"
    assert row["question"] == "What does popular mean?"
    assert row["status"] == "answered"
    assert row["raised_at"] is None
    assert row["certified"] is False


def test_a_refusal_clarification_becomes_certified_once_its_draft_is_approved(
    monkeypatch, tmp_path: Path
) -> None:
    from governed_bi.corpus.drafts import approve_draft, submit_draft
    from governed_bi.curator.clarification import draft_from_clarification
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        ClarificationRecordStatus,
        write_clarifications,
    )

    trace_store = _turn_log(monkeypatch, tmp_path)
    _log_turn(trace_store, "t1", "th-1")
    write_clarifications(
        tmp_path,
        [
            ClarificationRecord(
                id="refusal-1", scope="refusal:1", question="What does popular mean?",
                status=ClarificationRecordStatus.answered, answer="Highest downloads.",
                answered_by="user", source="refusal", basis="data_definition", turn_id="t1",
            )
        ],
    )
    client = _client(trace_store, _session_with_corpus_root(tmp_path))

    draft = draft_from_clarification("What does popular mean?", "Highest downloads.", schema=_DB_ID)
    submit_draft(tmp_path, draft, namespace=_DB_ID)
    approve_draft(tmp_path, draft.id)

    (row,) = client.get("/threads/th-1/raised").json()
    assert row["certified"] is True


def test_a_refusal_clarification_with_no_turn_id_is_excluded(monkeypatch, tmp_path: Path) -> None:
    """A row that predates B-0 has no `turn_id` at all -- there is no thread to trace it to, so
    it is excluded, not guessed at."""
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        ClarificationRecordStatus,
        write_clarifications,
    )

    trace_store = _turn_log(monkeypatch, tmp_path)
    write_clarifications(
        tmp_path,
        [
            ClarificationRecord(
                id="refusal-1", scope="refusal:1", question="What does popular mean?",
                status=ClarificationRecordStatus.answered, answer="Highest downloads.",
                answered_by="user", source="refusal", basis="data_definition",
            )
        ],
    )
    client = _client(trace_store, _session_with_corpus_root(tmp_path))
    assert client.get("/threads/th-1/raised").json() == []


def test_a_non_refusal_clarification_is_excluded_even_with_a_matching_turn(
    monkeypatch, tmp_path: Path
) -> None:
    """`raised_by` this reader means `source == "refusal"` -- an admin's own curator row, or a
    live ask_user question, was not raised by the person on this thread, no matter what turn_id
    it happens to carry."""
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        ClarificationRecordStatus,
        write_clarifications,
    )

    trace_store = _turn_log(monkeypatch, tmp_path)
    _log_turn(trace_store, "t1", "th-1")
    write_clarifications(
        tmp_path,
        [
            ClarificationRecord(
                id="live-1", scope="live:1", question="what does active mean?",
                status=ClarificationRecordStatus.answered, answer="90 days", answered_by="user",
                source="live_chat", basis="data_definition", turn_id="t1",
            )
        ],
    )
    client = _client(trace_store, _session_with_corpus_root(tmp_path))
    assert client.get("/threads/th-1/raised").json() == []


def test_both_kinds_at_once_are_sorted_and_both_present(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        ClarificationRecordStatus,
        write_clarifications,
    )
    from governed_bi.curator.feedback import FeedbackRecord, write_feedback

    trace_store = _turn_log(monkeypatch, tmp_path)
    _log_turn(trace_store, "t1", "th-1")
    _log_turn(trace_store, "t2", "th-1")
    write_feedback(
        tmp_path,
        [
            FeedbackRecord(
                id="f1", turn_id="t1", question="q1?", answer_text="a1.",
                reported_at="2026-08-01T00:00:00+00:00",
            )
        ],
    )
    write_clarifications(
        tmp_path,
        [
            ClarificationRecord(
                id="refusal-1", scope="refusal:1", question="q2?",
                status=ClarificationRecordStatus.answered, answer="a2.", answered_by="user",
                source="refusal", basis="data_definition", turn_id="t2",
            )
        ],
    )
    client = _client(trace_store, _session_with_corpus_root(tmp_path))

    rows = client.get("/threads/th-1/raised").json()
    assert {r["kind"] for r in rows} == {"feedback", "clarification"}
    assert len(rows) == 2
