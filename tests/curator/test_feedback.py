"""curator/feedback.py: the reader-reported-wrong-answer ledger and its fold (task H).

Mirrors tests/curator/test_clarifications.py's shape for the CRUD half and
tests/curator/test_clarification.py's shape for the fold half -- this module's whole argument
is that a report is a *different* record type from a clarification, so its tests are written
against a sibling ledger, never against clarifications.jsonl.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


def _certified_term(asset_id: str, summary: str) -> Any:
    from governed_bi.corpus.schema import Audit, Provenance, ProvenanceSource, ProvenanceStatus, TermAsset

    return TermAsset(
        id=asset_id,
        name=asset_id,
        summary=summary,
        audit=Audit(provenance=Provenance(source=ProvenanceSource.human, status=ProvenanceStatus.certified)),
    )


# ── round-trip JSONL persistence ────────────────────────────────────────────────────────────


def test_load_on_a_missing_file_is_an_empty_list(tmp_path: Path) -> None:
    from governed_bi.curator.feedback import load_feedback

    assert load_feedback(tmp_path) == []


def test_write_then_load_round_trips_every_field(tmp_path: Path) -> None:
    from governed_bi.curator.feedback import FeedbackRecord, FeedbackRecordStatus, load_feedback, write_feedback

    record = FeedbackRecord(
        id="feedback-1",
        turn_id="turn-1",
        question="Which apps are popular?",
        answer_text="The top 5 by rating are...",
        status=FeedbackRecordStatus.answered,
        reason="Rating isn't how we define popular.",
        reported_at="2026-08-16T00:00:00+00:00",
        correction="Popular means highest download count.",
        answered_by="admin@example.com",
        converted_to_corpus=True,
    )
    write_feedback(tmp_path, [record])

    (loaded,) = load_feedback(tmp_path)
    assert loaded == record


def test_write_creates_the_ledger_at_corpus_root_slash_feedback_jsonl(tmp_path: Path) -> None:
    from governed_bi.curator.feedback import FeedbackRecord, write_feedback

    write_feedback(
        tmp_path,
        [FeedbackRecord(id="f1", turn_id="t1", question="q?", answer_text="a.")],
    )
    # A sibling of clarifications.jsonl, never the same file (H-b: a different record type).
    assert (tmp_path / "feedback.jsonl").exists()
    assert not (tmp_path / "clarifications.jsonl").exists()


def test_load_rejects_an_unknown_field(tmp_path: Path) -> None:
    import pytest

    from governed_bi.curator.feedback import load_feedback

    path = tmp_path / "feedback.jsonl"
    path.write_text(
        '{"id": "f1", "turn_id": "t1", "question": "q?", "answer_text": "a.", "bogus_field": 1}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bogus_field"):
        load_feedback(tmp_path)


# ── file_report ──────────────────────────────────────────────────────────────────────────────


def test_file_report_appends_an_open_record(tmp_path: Path) -> None:
    from governed_bi.curator.feedback import FeedbackRecordStatus, file_report, load_feedback

    record = file_report(
        tmp_path, turn_id="turn-1", question="Which apps are popular?", answer_text="Top 5 by rating.",
    )
    assert record.status is FeedbackRecordStatus.open
    assert record.turn_id == "turn-1"
    assert record.reported_at is not None
    assert load_feedback(tmp_path) == [record]


def test_file_report_records_the_optional_reason(tmp_path: Path) -> None:
    from governed_bi.curator.feedback import file_report

    record = file_report(
        tmp_path, turn_id="t1", question="q?", answer_text="a.", reason="That's not how we count active.",
    )
    assert record.reason == "That's not how we count active."


def test_file_report_with_no_reason_is_none(tmp_path: Path) -> None:
    from governed_bi.curator.feedback import file_report

    record = file_report(tmp_path, turn_id="t1", question="q?", answer_text="a.")
    assert record.reason is None


def test_filing_the_identical_report_twice_returns_the_existing_row(tmp_path: Path) -> None:
    """Idempotent by content -- a network retry of the same click must not double the queue."""
    from governed_bi.curator.feedback import file_report, load_feedback

    first = file_report(tmp_path, turn_id="t1", question="q?", answer_text="a.")
    second = file_report(tmp_path, turn_id="t1", question="q?", answer_text="a.")

    assert first.id == second.id
    assert len(load_feedback(tmp_path)) == 1


def test_filing_a_different_answer_text_for_the_same_turn_is_a_second_row(tmp_path: Path) -> None:
    from governed_bi.curator.feedback import file_report, load_feedback

    file_report(tmp_path, turn_id="t1", question="q?", answer_text="a.")
    file_report(tmp_path, turn_id="t1", question="q?", answer_text="a different answer.")

    assert len(load_feedback(tmp_path)) == 2


# ── answer_report ────────────────────────────────────────────────────────────────────────────


def test_answer_report_sets_status_and_correction_fields(tmp_path: Path) -> None:
    from governed_bi.curator.feedback import (
        FeedbackRecordStatus,
        answer_report,
        file_report,
        load_feedback,
    )

    filed = file_report(tmp_path, turn_id="t1", question="q?", answer_text="a.")
    updated = answer_report(tmp_path, filed.id, correction="The real answer.", answered_by="admin")

    assert updated.status is FeedbackRecordStatus.answered
    assert updated.correction == "The real answer."
    assert updated.answered_by == "admin"
    (on_disk,) = load_feedback(tmp_path)
    assert on_disk == updated


def test_answer_report_unknown_id_raises(tmp_path: Path) -> None:
    import pytest

    from governed_bi.curator.feedback import FeedbackNotFound, answer_report

    with pytest.raises(FeedbackNotFound):
        answer_report(tmp_path, "nope", correction="x")


# ── dismiss_report ───────────────────────────────────────────────────────────────────────────


def test_dismiss_report_sets_status_unconditionally(tmp_path: Path) -> None:
    from governed_bi.curator.feedback import FeedbackRecordStatus, dismiss_report, file_report

    filed = file_report(tmp_path, turn_id="t1", question="q?", answer_text="a.")
    updated = dismiss_report(tmp_path, filed.id)
    assert updated.status is FeedbackRecordStatus.dismissed


def test_dismiss_report_unknown_id_raises(tmp_path: Path) -> None:
    import pytest

    from governed_bi.curator.feedback import FeedbackNotFound, dismiss_report

    with pytest.raises(FeedbackNotFound):
        dismiss_report(tmp_path, "nope")


def test_dismiss_report_on_an_answered_record_is_refused(tmp_path: Path) -> None:
    """Its correction may already be folded into the corpus under an id hashed from this
    report's own text -- mirrors cancel_clarification's identical refusal."""
    import pytest

    from governed_bi.curator.feedback import (
        FeedbackRecordStatus,
        answer_report,
        dismiss_report,
        file_report,
        load_feedback,
    )

    filed = file_report(tmp_path, turn_id="t1", question="q?", answer_text="a.")
    answer_report(tmp_path, filed.id, correction="The real answer.")

    with pytest.raises(ValueError, match="already answered"):
        dismiss_report(tmp_path, filed.id)

    (on_disk,) = load_feedback(tmp_path)
    assert on_disk.status is FeedbackRecordStatus.answered


# ── fold_report_into_corpus: the Enhancer path, and the source/id-prefix decisions (H-4) ────


def test_fold_report_into_corpus_writes_a_novel_proposed_draft(tmp_path: Path) -> None:
    from governed_bi.corpus.schema import ProvenanceStatus
    from governed_bi.corpus.store import load
    from governed_bi.curator.feedback import answer_report, file_report, fold_report_into_corpus

    filed = file_report(tmp_path, turn_id="t1", question="what does active mean?", answer_text="wrong answer")
    answered = answer_report(tmp_path, filed.id, correction="90 days")
    fold_report_into_corpus(
        answered, agent_model=None, corpus_root=tmp_path, schema="olist", known_assets=(),
    )
    (draft,) = load(tmp_path)[0]
    assert draft.asset_type.value == "term"
    assert "90 days" in draft.summary
    assert draft.audit is not None and draft.audit.provenance is not None
    assert draft.audit.provenance.status is ProvenanceStatus.proposed


def test_fold_report_into_corpus_mints_a_feedback_prefixed_id_not_a_clarification_one(
    tmp_path: Path,
) -> None:
    """H-4's id-prefix decision, made mechanical: a report's draft must not collide with
    `_is_clarification_derived`'s `clarification.` check, so it never shows up in
    `/corpus/assumptions` under the clarification ledger's own name for itself."""
    from governed_bi.corpus.store import load
    from governed_bi.curator.feedback import answer_report, file_report, fold_report_into_corpus

    filed = file_report(tmp_path, turn_id="t1", question="what does active mean?", answer_text="wrong")
    answered = answer_report(tmp_path, filed.id, correction="90 days")
    fold_report_into_corpus(
        answered, agent_model=None, corpus_root=tmp_path, schema="olist", known_assets=(),
    )
    (draft,) = load(tmp_path)[0]
    assert draft.id.startswith("feedback.olist.")
    assert not draft.id.startswith("clarification.")


def test_fold_report_into_corpus_stamps_source_feedback(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load
    from governed_bi.curator.feedback import answer_report, file_report, fold_report_into_corpus

    filed = file_report(tmp_path, turn_id="t1", question="q?", answer_text="wrong")
    answered = answer_report(tmp_path, filed.id, correction="right answer")
    fold_report_into_corpus(
        answered, agent_model=None, corpus_root=tmp_path, schema="olist", known_assets=(),
    )
    (draft,) = load(tmp_path)[0]
    assert draft.audit.extra["source"] == "feedback"


def test_fold_report_into_corpus_marks_converted_and_is_idempotent(tmp_path: Path) -> None:
    from governed_bi.corpus.store import load
    from governed_bi.curator.feedback import (
        answer_report,
        file_report,
        fold_report_into_corpus,
        load_feedback,
    )

    filed = file_report(tmp_path, turn_id="t1", question="q?", answer_text="wrong")
    answered = answer_report(tmp_path, filed.id, correction="right answer")
    once = fold_report_into_corpus(
        answered, agent_model=None, corpus_root=tmp_path, schema="olist", known_assets=(),
    )
    assert once.converted_to_corpus is True

    twice = fold_report_into_corpus(
        once, agent_model=None, corpus_root=tmp_path, schema="olist", known_assets=load(tmp_path)[0],
    )
    assert twice == once

    assets, _ = load(tmp_path)
    assert len(assets) == 1, f"the fold ran twice: {[a.id for a in assets]}"
    (on_disk,) = load_feedback(tmp_path)
    assert on_disk.converted_to_corpus is True


def test_fold_report_into_corpus_duplicate_of_a_certified_term_writes_no_new_file(tmp_path: Path) -> None:
    from test_clarification import _scripted  # sibling fixture, as tests/api/test_elicitation_rescan.py does

    from governed_bi.corpus.store import load, write
    from governed_bi.curator.feedback import answer_report, file_report, fold_report_into_corpus

    existing = _certified_term("feedback.olist.existing1", "active customer — placed an order in 90 days")
    write(tmp_path, existing, namespace="olist")

    filed = file_report(tmp_path, turn_id="t1", question="what does active mean?", answer_text="wrong")
    answered = answer_report(tmp_path, filed.id, correction="an active customer ordered in the last 90 days")
    fold_report_into_corpus(
        answered,
        agent_model=_scripted(f'{{"duplicate_of": "{existing.id}", "conflict_with": null}}'),
        corpus_root=tmp_path,
        schema="olist",
        known_assets=[existing],
    )
    assets, problems = load(tmp_path)
    assert not problems
    assert assets == [existing]  # nothing new was minted


def test_fold_report_into_corpus_enhancer_error_falls_back_to_unconditional_write(tmp_path: Path) -> None:
    from test_clarification import _scripted  # sibling fixture, as tests/api/test_elicitation_rescan.py does

    from governed_bi.corpus.store import load, write
    from governed_bi.curator.feedback import answer_report, file_report, fold_report_into_corpus

    existing = _certified_term("feedback.olist.existing2", "some other fact")
    write(tmp_path, existing, namespace="olist")

    filed = file_report(tmp_path, turn_id="t1", question="q?", answer_text="wrong")
    answered = answer_report(tmp_path, filed.id, correction="right answer")
    fold_report_into_corpus(
        answered,
        agent_model=_scripted("not json at all"),
        corpus_root=tmp_path,
        schema="olist",
        known_assets=[existing],
    )
    assets, problems = load(tmp_path)
    assert not problems
    new_drafts = [a for a in assets if a.id != existing.id]
    assert len(new_drafts) == 1


def test_fold_report_into_corpus_with_no_correction_is_a_no_op(tmp_path: Path) -> None:
    """A record with no ``correction`` (never answered, or answered with an empty string some
    caller let through) is returned untouched -- no draft, no ledger write, no exception, even
    against a corpus root that could not be written to if this tried."""
    from governed_bi.curator.feedback import FeedbackRecord, fold_report_into_corpus

    unanswered = FeedbackRecord(id="f1", turn_id="t1", question="q?", answer_text="a.", correction=None)
    result = fold_report_into_corpus(
        unanswered, agent_model=None, corpus_root="/nonexistent/path/xyz", schema="olist", known_assets=(),
    )
    assert result is unanswered
