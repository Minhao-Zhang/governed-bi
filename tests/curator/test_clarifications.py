"""curator/clarifications.py: the offline clarifications ledger (round trip + answer)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


def _record(**overrides):
    from governed_bi.curator.clarifications import ClarificationRecord

    defaults = dict(id="q001", scope="table:orders", question="what counts as active?")
    defaults.update(overrides)
    return ClarificationRecord(**defaults)


# ── round-trip JSONL persistence ────────────────────────────────────────────────────────────


def test_load_on_a_missing_file_is_an_empty_list(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import load_clarifications

    assert load_clarifications(tmp_path) == []


def test_write_then_load_round_trips_every_field(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import (
        ClarificationRecordStatus,
        load_clarifications,
        write_clarifications,
    )

    record = _record(
        status=ClarificationRecordStatus.answered,
        raised_by=("analyst_v6",),
        choices=({"id": "opt_a", "label": "90 days"}, {"id": "opt_b", "label": "30 days"}),
        allow_freeform=False,
        answer="90 days, picked opt_a",
        answer_choice_id="opt_a",
        answer_choice_ids=("opt_a",),
        answered_by="admin@example.com",
        converted_to_corpus=True,
        source="live_chat",
        category="A",
        ui_modality="column_picker",
        target_table="orders",
        target_column="status",
    )
    write_clarifications(tmp_path, [record])

    (loaded,) = load_clarifications(tmp_path)
    assert loaded == record


def test_write_creates_the_ledger_at_corpus_root_slash_clarifications_jsonl(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import write_clarifications

    write_clarifications(tmp_path, [_record()])
    assert (tmp_path / "clarifications.jsonl").exists()


def test_write_overwrites_the_whole_file(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import load_clarifications, write_clarifications

    write_clarifications(tmp_path, [_record(id="q001"), _record(id="q002")])
    write_clarifications(tmp_path, [_record(id="q003")])

    (loaded,) = load_clarifications(tmp_path)
    assert loaded.id == "q003"


def test_load_rejects_an_unknown_field(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import load_clarifications

    path = tmp_path / "clarifications.jsonl"
    path.write_text('{"id": "q1", "scope": "s", "question": "q?", "bogus_field": 1}\n', encoding="utf-8")
    import pytest

    with pytest.raises(ValueError, match="bogus_field"):
        load_clarifications(tmp_path)


# ── resolve_answer_text ──────────────────────────────────────────────────────────────────────


def test_resolve_answer_text_prefers_the_picked_choice_label() -> None:
    from governed_bi.curator.clarifications import resolve_answer_text

    record = _record(
        choices=({"id": "opt_a", "label": "90 days"},),
        answer_choice_id="opt_a",
    )
    assert resolve_answer_text(record) == "90 days"


def test_resolve_answer_text_appends_freeform_to_the_picked_label() -> None:
    from governed_bi.curator.clarifications import resolve_answer_text

    record = _record(
        choices=({"id": "opt_a", "label": "90 days"},),
        answer_choice_id="opt_a",
        answer="but exclude refunds",
    )
    assert resolve_answer_text(record) == "90 days — but exclude refunds"


def test_resolve_answer_text_falls_through_to_freeform_when_no_choice_matches() -> None:
    from governed_bi.curator.clarifications import resolve_answer_text

    record = _record(
        choices=({"id": "opt_a", "label": "90 days"},),
        answer_choice_id="does_not_exist",
        answer="freeform instead",
    )
    assert resolve_answer_text(record) == "freeform instead"


def test_resolve_answer_text_is_none_with_neither_choice_nor_answer() -> None:
    from governed_bi.curator.clarifications import resolve_answer_text

    assert resolve_answer_text(_record()) is None


# ── answer_clarification ─────────────────────────────────────────────────────────────────────


def test_answer_clarification_sets_status_and_answer_fields(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import (
        ClarificationRecordStatus,
        answer_clarification,
        load_clarifications,
        write_clarifications,
    )

    write_clarifications(tmp_path, [_record(id="q001")])
    updated = answer_clarification(tmp_path, "q001", answer="90 days", answered_by="admin")

    assert updated.status is ClarificationRecordStatus.answered
    assert updated.answer == "90 days"
    assert updated.answered_by == "admin"

    (on_disk,) = load_clarifications(tmp_path)
    assert on_disk == updated


def test_answer_clarification_with_a_choice_id(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import answer_clarification, write_clarifications

    write_clarifications(
        tmp_path, [_record(id="q001", choices=({"id": "opt_a", "label": "90 days"},))]
    )
    updated = answer_clarification(tmp_path, "q001", choice_id="opt_a")
    assert updated.answer_choice_id == "opt_a"
    assert updated.answer is None


def test_answer_clarification_unknown_id_raises(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import ClarificationNotFound, answer_clarification

    import pytest

    with pytest.raises(ClarificationNotFound):
        answer_clarification(tmp_path, "nope", answer="x")


def test_answer_clarification_leaves_other_records_untouched(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import (
        ClarificationRecordStatus,
        answer_clarification,
        load_clarifications,
        write_clarifications,
    )

    write_clarifications(tmp_path, [_record(id="q001"), _record(id="q002")])
    answer_clarification(tmp_path, "q001", answer="90 days")

    by_id = {r.id: r for r in load_clarifications(tmp_path)}
    assert by_id["q001"].status is ClarificationRecordStatus.answered
    assert by_id["q002"].status is ClarificationRecordStatus.open
