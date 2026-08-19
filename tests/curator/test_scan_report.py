"""curator/scan_report.py: what a re-run tells an admin changed since the last one.

The owner's third standing decision (``detent-ai-setup-wizard-gap-model.md`` § "Three owner
decisions"): a re-run diffs against already-confirmed content and, when nothing is new, **says
so**. These tests assert the *account*, not the counts — the summary sentence is the deliverable
and is pinned verbatim, because a status line an admin misreads is worse than no status line.

Records here are hand-built rather than generated. The diff is pure set arithmetic over
``(scope, status, unmet_prerequisites_at_answer, source)`` and nothing about a real schema makes
any of those cases reachable that a literal record does not; the generated path is exercised end
to end through the route in ``tests/api/test_elicitation_rescan.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


def _rec(scope: str, **changes: Any) -> Any:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import ELICITATION_SOURCE, _record_id

    fields: dict[str, Any] = {
        "id": _record_id(scope),
        "scope": scope,
        "question": f"question about {scope}",
        "source": ELICITATION_SOURCE,
        "severity": "T2",
        "audience": "data",
        "category": "B",
    }
    fields.update(changes)
    return ClarificationRecord(**fields)


def _answered(scope: str, *, unmet: tuple[str, ...] = (), **changes: Any) -> Any:
    from governed_bi.curator.clarifications import ClarificationRecordStatus

    return _rec(
        scope,
        status=ClarificationRecordStatus.answered,
        answer="something",
        unmet_prerequisites_at_answer=unmet,
        **changes,
    )


# ── the buckets ─────────────────────────────────────────────────────────────────────────────


def test_a_first_scan_reports_everything_as_new_and_nothing_to_compare_against() -> None:
    from governed_bi.curator.scan_report import diff_scan_against_ledger

    scanned = [_rec("elicitation:valuemap:t.a"), _rec("elicitation:valuemap:t.b")]
    report = diff_scan_against_ledger(scanned, [], [])

    assert [r.scope for r in report.new] == [r.scope for r in scanned]
    assert report.still_open == () and report.settled == () and report.stranded == ()
    assert report.nothing_new is False


def test_a_second_scan_with_nothing_changed_reports_no_new_and_carries_the_rest_forward() -> None:
    """The state the decision is about. The scan re-derives the identical candidates; every one
    of them is already on the ledger, so ``new`` is empty **and that emptiness is a statement**
    rather than something a client has to interpret."""
    from governed_bi.curator.scan_report import diff_scan_against_ledger

    scanned = [_rec("elicitation:valuemap:t.a"), _rec("elicitation:valuemap:t.b")]
    report = diff_scan_against_ledger(scanned, [], scanned)

    assert report.new == ()
    assert report.nothing_new is True
    assert {r.scope for r in report.still_open} == {r.scope for r in scanned}


def test_answering_one_question_moves_exactly_that_one_from_still_open_to_settled() -> None:
    from governed_bi.curator.scan_report import diff_scan_against_ledger

    scanned = [_rec("elicitation:valuemap:t.a"), _rec("elicitation:valuemap:t.b")]
    ledger = [_answered("elicitation:valuemap:t.a"), _rec("elicitation:valuemap:t.b")]
    report = diff_scan_against_ledger(scanned, [], ledger)

    assert [r.scope for r in report.settled] == ["elicitation:valuemap:t.a"]
    assert [r.scope for r in report.still_open] == ["elicitation:valuemap:t.b"]
    assert report.new == ()


def test_a_question_the_corpus_answers_with_no_ledger_row_still_counts_as_settled() -> None:
    """The design doc's ``beer_factory`` observation, made reportable: a fact settled by a live
    clarification or by hand curation is invisible to the wizard's ledger, and the dedup dropping
    the candidate is the only trace of it. Absent this, that question would be absent from every
    bucket — the exact "silently returning an empty list" the decision forbids."""
    from governed_bi.curator.scan_report import diff_scan_against_ledger

    settled_elsewhere = _rec("elicitation:exclusion:t.status")
    report = diff_scan_against_ledger([], [settled_elsewhere], [])

    assert [r.scope for r in report.settled] == ["elicitation:exclusion:t.status"]
    assert report.nothing_new is True


def test_a_wizard_answer_settled_in_both_places_is_counted_once() -> None:
    from governed_bi.curator.scan_report import diff_scan_against_ledger

    scope = "elicitation:valuemap:t.a"
    report = diff_scan_against_ledger([], [_rec(scope)], [_answered(scope)])

    assert len(report.settled) == 1


def test_an_answer_given_without_its_prerequisite_is_stranded_not_settled() -> None:
    """``f718365`` made that answer fold at ``draft``; ``approve_draft`` accepts ``proposed``
    only and nothing promotes a ``draft`` back. Counting it as settled would tell an admin a
    question is closed when what actually happened is that it is stuck, uncertifiable, and — per
    ``detent-ai-design-gaps`` #4 — unreachable by any edit path this wizard has."""
    from governed_bi.curator.scan_report import diff_scan_against_ledger

    ledger = [
        _answered("elicitation:termcolumn:price", unmet=("elicit.abc",)),
        _answered("elicitation:valuemap:t.a"),
    ]
    report = diff_scan_against_ledger([], [], ledger)

    assert [r.scope for r in report.stranded] == ["elicitation:termcolumn:price"]
    assert [r.scope for r in report.settled] == ["elicitation:valuemap:t.a"]


def test_a_non_wizard_ledger_row_is_none_of_this_scans_business() -> None:
    """``clarifications.jsonl`` is one file for four sources. A mid-turn ``ask_user`` record
    sitting in it is not an onboarding gap, and reporting one as "still open from an earlier
    scan" would put a live-chat question into an admin's setup report."""
    from governed_bi.curator.scan_report import diff_scan_against_ledger

    live = _rec("live:whatever", source="live_chat", category=None)
    report = diff_scan_against_ledger([], [], [live])

    assert report.still_open == () and report.settled == ()


def test_a_refusal_sourced_ledger_row_is_also_none_of_this_scans_business() -> None:
    """A reader's own refusal-originated clarification (task A) is not a wizard candidate
    either -- it was never presented by a scan, so counting it as "still open" or "settled"
    would put a reader's report into an admin's onboarding-coverage report."""
    from governed_bi.curator.scan_report import diff_scan_against_ledger

    refusal = _rec(
        "refusal:whatever", source="refusal", basis="data_definition", category=None
    )
    report = diff_scan_against_ledger([], [], [refusal])

    assert report.still_open == () and report.settled == ()


# ── the sentence ────────────────────────────────────────────────────────────────────────────


def test_the_first_scan_says_there_was_nothing_to_compare_against() -> None:
    from governed_bi.curator.scan_report import diff_scan_against_ledger, scan_summary

    scanned = [
        _rec("elicitation:duplicate:t.a|t.b", severity="T1"),
        _rec("elicitation:valuemap:t.c"),
        _rec("elicitation:describetable:t", severity="T4"),
    ]
    assert scan_summary(diff_scan_against_ledger(scanned, [], [])) == (
        "Found 3 new gaps — 1 T1, 1 T2, 1 T4. "
        "Nothing was on file before this scan to compare against."
    )


def test_a_re_run_that_finds_nothing_says_so_in_words() -> None:
    """The verbatim sentence the owner's decision asks for, and the reason it is composed on the
    backend: this string is the deliverable, and a second copy of it in TypeScript is a second
    thing that has to stay true."""
    from governed_bi.curator.scan_report import diff_scan_against_ledger, scan_summary

    scanned = [
        _rec("elicitation:duplicate:t.a|t.b", severity="T1"),
        _rec("elicitation:valuemap:t.c"),
    ]
    ledger = [*scanned, _answered("elicitation:exclusion:t.status")]
    assert scan_summary(diff_scan_against_ledger(scanned, [], ledger)) == (
        "No new gaps found. "
        "2 questions from an earlier scan are still unanswered (1 of them T1). "
        "1 question was already answered and was not asked again."
    )


def test_the_stranded_clause_says_what_is_stuck_and_does_not_promise_a_fix() -> None:
    from governed_bi.curator.scan_report import diff_scan_against_ledger, scan_summary

    ledger = [_answered("elicitation:termcolumn:price", unmet=("elicit.abc",))]
    assert scan_summary(diff_scan_against_ledger([], [], ledger)) == (
        "No new gaps found. "
        "1 answer landed before the question it depends on: recorded as a draft nobody can "
        "certify, and this wizard cannot yet reopen it."
    )


def test_one_of_everything_reads_as_one_paragraph() -> None:
    from governed_bi.curator.scan_report import diff_scan_against_ledger, scan_summary

    scanned = [_rec("elicitation:valuemap:t.new", severity="T2")]
    ledger = [
        _rec("elicitation:duplicate:t.a|t.b", severity="T1"),
        _answered("elicitation:exclusion:t.status"),
        _answered("elicitation:termcolumn:price", unmet=("elicit.abc",)),
    ]
    assert scan_summary(diff_scan_against_ledger(scanned, [], ledger)) == (
        "Found 1 new gap — 1 T2. "
        "1 question from an earlier scan is still unanswered (1 of them T1). "
        "1 question was already answered and was not asked again. "
        "1 answer landed before the question it depends on: recorded as a draft nobody can "
        "certify, and this wizard cannot yet reopen it."
    )


# ── the wire shape ──────────────────────────────────────────────────────────────────────────


def test_the_payload_states_nothing_new_rather_than_leaving_it_to_be_inferred() -> None:
    from governed_bi.curator.scan_report import diff_scan_against_ledger, scan_report_payload

    scope = "elicitation:valuemap:t.a"
    payload = scan_report_payload(diff_scan_against_ledger([], [], [_rec(scope)]))

    assert payload["nothing_new"] is True
    assert payload["new"] == {"count": 0, "by_severity": {}, "scopes": []}
    assert payload["still_open"] == {"count": 1, "by_severity": {"T2": 1}, "scopes": [scope]}
    assert payload["summary"].startswith("No new gaps found.")


def test_severity_counts_are_worst_tier_first_and_omit_empty_tiers() -> None:
    from governed_bi.curator.scan_report import diff_scan_against_ledger, scan_report_payload

    scanned = [
        _rec("elicitation:valuemap:t.a", severity="T4"),
        _rec("elicitation:duplicate:t.a|t.b", severity="T1"),
        _rec("elicitation:valuemap:t.c", severity="T4"),
    ]
    payload = scan_report_payload(diff_scan_against_ledger(scanned, [], []))
    assert list(payload["new"]["by_severity"].items()) == [("T1", 1), ("T4", 2)]
