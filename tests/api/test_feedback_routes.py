"""POST /feedback, GET /feedback, POST /feedback/{id}/answer, POST /feedback/{id}/dismiss
(detent-ai-trust-loop-plan.md, task H) -- the reader-reported-wrong-answer inbox, over its own
``feedback.jsonl`` ledger, mounted by ``api/feedback_routes.py::make_feedback_router``.

Modeled directly on ``tests/api/test_clarifications_route.py``: same session fixture shape, same
``needs("D")`` gate, same "seed the ledger, hit the route, assert on the response and the file"
structure -- but against a sibling ledger the clarification tests never touch, per H-b.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


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
        prompt_set_hash="p", knobs_resolved={}, db_id="beer", run_id="r",
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
        prompt_set_hash="p", knobs_resolved={}, db_id="beer", run_id="r",
        corpus_root=None,
    )


def _client(monkeypatch, session):
    from fastapi.testclient import TestClient

    from governed_bi.api import routes

    return TestClient(routes.make_app(session, None))


def _seed(tmp_path: Path, *records) -> None:
    from governed_bi.curator.feedback import write_feedback

    write_feedback(tmp_path, list(records))


# ── POST /feedback ───────────────────────────────────────────────────────────────────────────


def test_filing_a_report_with_no_corpus_root_is_409(monkeypatch) -> None:
    client = _client(monkeypatch, _session_without_corpus_root())
    response = client.post(
        "/feedback",
        json={"turn_id": "t1", "question": "Which apps are popular?", "answer_text": "Top 5 by rating."},
    )
    assert response.status_code == 409


def test_filing_a_report_with_no_turn_id_is_422(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post(
        "/feedback", json={"question": "Which apps are popular?", "answer_text": "Top 5 by rating."}
    )
    assert response.status_code == 422


def test_filing_a_report_with_no_question_is_422(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post("/feedback", json={"turn_id": "t1", "answer_text": "Top 5 by rating."})
    assert response.status_code == 422


def test_filing_a_report_with_no_answer_text_is_422(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post("/feedback", json={"turn_id": "t1", "question": "Which apps are popular?"})
    assert response.status_code == 422


def test_filing_a_report_creates_an_open_record(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.feedback import load_feedback

    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post(
        "/feedback",
        json={
            "turn_id": "t1",
            "question": "Which apps are popular?",
            "answer_text": "Top 5 by rating.",
            "reason": "Rating isn't how we define popular.",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "open"
    assert body["turn_id"] == "t1"
    assert body["reason"] == "Rating isn't how we define popular."
    assert body["reported_at"] is not None
    assert body["correction"] is None
    assert body["converted_to_corpus"] is False

    (on_disk,) = load_feedback(tmp_path)
    assert on_disk.id == body["id"]


def test_filing_a_report_with_no_reason_is_none(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post(
        "/feedback", json={"turn_id": "t1", "question": "q?", "answer_text": "a."}
    )
    assert response.json()["reason"] is None


def test_filing_the_same_report_twice_does_not_duplicate_the_ledger_row(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.feedback import load_feedback

    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    body = {"turn_id": "t1", "question": "q?", "answer_text": "a."}

    first = client.post("/feedback", json=body)
    second = client.post("/feedback", json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(load_feedback(tmp_path)) == 1


# ── GET /feedback ────────────────────────────────────────────────────────────────────────────


def test_get_returns_empty_list_with_no_corpus_root(monkeypatch) -> None:
    client = _client(monkeypatch, _session_without_corpus_root())
    response = client.get("/feedback")
    assert response.status_code == 200
    assert response.json() == []


def test_get_returns_empty_list_with_no_ledger_file(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.get("/feedback")
    assert response.status_code == 200
    assert response.json() == []


def test_get_lists_every_record_with_full_shape(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.feedback import FeedbackRecord

    _seed(
        tmp_path,
        FeedbackRecord(id="f1", turn_id="t1", question="Which apps are popular?", answer_text="Top 5."),
    )
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.get("/feedback")
    assert response.status_code == 200, response.text
    (row,) = response.json()
    assert row["id"] == "f1"
    assert row["turn_id"] == "t1"
    assert row["question"] == "Which apps are popular?"
    assert row["answer_text"] == "Top 5."
    assert row["status"] == "open"
    assert row["correction"] is None


def test_get_with_status_filter_narrows_the_list(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.feedback import FeedbackRecord, FeedbackRecordStatus

    _seed(
        tmp_path,
        FeedbackRecord(id="f1", turn_id="t1", question="q1?", answer_text="a1.", status=FeedbackRecordStatus.open),
        FeedbackRecord(
            id="f2", turn_id="t2", question="q2?", answer_text="a2.",
            status=FeedbackRecordStatus.dismissed,
        ),
    )
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))

    open_only = client.get("/feedback?status=open").json()
    assert [r["id"] for r in open_only] == ["f1"]

    dismissed_only = client.get("/feedback?status=dismissed").json()
    assert [r["id"] for r in dismissed_only] == ["f2"]


# ── POST /feedback/{id}/answer ───────────────────────────────────────────────────────────────


def test_answer_with_no_corpus_root_is_409(monkeypatch) -> None:
    client = _client(monkeypatch, _session_without_corpus_root())
    response = client.post("/feedback/f1/answer", json={"correction": "The real answer."})
    assert response.status_code == 409


def test_answer_with_no_correction_is_422(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.feedback import FeedbackRecord

    _seed(tmp_path, FeedbackRecord(id="f1", turn_id="t1", question="q?", answer_text="a."))
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post("/feedback/f1/answer", json={})
    assert response.status_code == 422


def test_answer_unknown_id_is_404(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post("/feedback/nope/answer", json={"correction": "x"})
    assert response.status_code == 404


def test_answer_sets_status_and_correction_and_folds_into_a_proposed_draft(
    monkeypatch, tmp_path: Path
) -> None:
    from governed_bi.corpus.schema import ProvenanceStatus
    from governed_bi.corpus.store import load
    from governed_bi.curator.feedback import FeedbackRecord

    _seed(
        tmp_path,
        FeedbackRecord(id="f1", turn_id="t1", question="what does active mean?", answer_text="wrong answer"),
    )
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))

    response = client.post("/feedback/f1/answer", json={"correction": "90 days"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "answered"
    assert body["correction"] == "90 days"
    assert body["answered_by"] == "admin"
    assert body["converted_to_corpus"] is True

    assets, problems = load(tmp_path)
    assert not problems
    (draft,) = assets
    assert draft.id.startswith("feedback.beer.")
    assert "90 days" in draft.summary
    assert draft.audit.provenance.status is ProvenanceStatus.proposed
    assert draft.audit.extra["source"] == "feedback"


def test_answer_respects_a_custom_answered_by(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.feedback import FeedbackRecord

    _seed(tmp_path, FeedbackRecord(id="f1", turn_id="t1", question="q?", answer_text="wrong"))
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))

    response = client.post(
        "/feedback/f1/answer", json={"correction": "right", "answered_by": "someone@example.com"}
    )
    assert response.json()["answered_by"] == "someone@example.com"


def test_answering_the_same_report_twice_does_not_double_write(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.corpus.store import load
    from governed_bi.curator.feedback import FeedbackRecord

    _seed(tmp_path, FeedbackRecord(id="f1", turn_id="t1", question="q?", answer_text="wrong"))
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))

    first = client.post("/feedback/f1/answer", json={"correction": "right"})
    assert first.json()["converted_to_corpus"] is True

    second = client.post("/feedback/f1/answer", json={"correction": "right, still"})
    assert second.status_code == 200, second.text

    assets, _ = load(tmp_path)
    assert len(assets) == 1, f"the fold ran twice: {[a.id for a in assets]}"


def test_answered_reports_do_not_show_up_at_corpus_assumptions(monkeypatch, tmp_path: Path) -> None:
    """H-4's id-prefix decision, verified over the real /corpus/assumptions route: a report's
    correction is not a clarification, so it must not be reported under that route's own name
    for a settled clarification Q&A. `_is_clarification_derived` keys on the `clarification.`
    id prefix; `fold_report_into_corpus` never mints one."""
    from governed_bi.curator.feedback import FeedbackRecord

    _seed(
        tmp_path,
        FeedbackRecord(id="f1", turn_id="t1", question="what does active mean?", answer_text="wrong"),
    )
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    client.post("/feedback/f1/answer", json={"correction": "90 days"})

    assert client.get("/corpus/assumptions").json() == []


def test_answered_reports_show_up_in_the_drafts_queue(monkeypatch, tmp_path: Path) -> None:
    """The other half of H-4: the corpus draft still reaches the existing, prefix-agnostic
    Drafts queue (task D) -- the one surface an admin actually approves from."""
    from governed_bi.curator.feedback import FeedbackRecord

    _seed(
        tmp_path,
        FeedbackRecord(id="f1", turn_id="t1", question="what does active mean?", answer_text="wrong"),
    )
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    client.post("/feedback/f1/answer", json={"correction": "90 days"})

    (row,) = client.get("/corpus/drafts").json()
    assert row["provenance_status"] == "proposed"
    assert "90 days" in row["summary"]


# ── POST /feedback/{id}/dismiss ──────────────────────────────────────────────────────────────


def test_dismiss_with_no_corpus_root_is_409(monkeypatch) -> None:
    client = _client(monkeypatch, _session_without_corpus_root())
    response = client.post("/feedback/f1/dismiss")
    assert response.status_code == 409


def test_dismiss_unknown_id_is_404(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    response = client.post("/feedback/nope/dismiss")
    assert response.status_code == 404


def test_dismiss_sets_status_and_takes_no_body(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.feedback import FeedbackRecord

    _seed(tmp_path, FeedbackRecord(id="f1", turn_id="t1", question="q?", answer_text="a."))
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))

    response = client.post("/feedback/f1/dismiss")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "dismissed"


def test_dismissing_an_answered_report_is_409(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.feedback import FeedbackRecord, FeedbackRecordStatus, load_feedback

    _seed(tmp_path, FeedbackRecord(id="f1", turn_id="t1", question="q?", answer_text="a."))
    client = _client(monkeypatch, _session_with_corpus_root(tmp_path))
    client.post("/feedback/f1/answer", json={"correction": "the real answer"})

    response = client.post("/feedback/f1/dismiss")
    assert response.status_code == 409, response.text

    (on_disk,) = load_feedback(tmp_path)
    assert on_disk.status is FeedbackRecordStatus.answered
