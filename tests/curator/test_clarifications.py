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
        basis="data_definition",
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


# ── basis (Phase 1c gap fix) ─────────────────────────────────────────────────────────────────


def test_basis_defaults_to_none() -> None:
    assert _record().basis is None


def test_basis_round_trips_through_the_ledger(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import load_clarifications, write_clarifications

    write_clarifications(tmp_path, [_record(basis="ranking_ambiguity")])
    (loaded,) = load_clarifications(tmp_path)
    assert loaded.basis == "ranking_ambiguity"


# ── mark_converted_to_corpus ─────────────────────────────────────────────────────────────────


def test_mark_converted_to_corpus_sets_the_flag_and_persists(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import load_clarifications, mark_converted_to_corpus, write_clarifications

    write_clarifications(tmp_path, [_record(id="q001")])
    updated = mark_converted_to_corpus(tmp_path, "q001")

    assert updated.converted_to_corpus is True
    (on_disk,) = load_clarifications(tmp_path)
    assert on_disk.converted_to_corpus is True


def test_mark_converted_to_corpus_unknown_id_raises(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import ClarificationNotFound, mark_converted_to_corpus

    import pytest

    with pytest.raises(ClarificationNotFound):
        mark_converted_to_corpus(tmp_path, "nope")


def test_mark_converted_to_corpus_leaves_other_fields_untouched(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import mark_converted_to_corpus, write_clarifications

    write_clarifications(tmp_path, [_record(id="q001", answer="90 days", basis="data_definition")])
    updated = mark_converted_to_corpus(tmp_path, "q001")

    assert updated.answer == "90 days"
    assert updated.basis == "data_definition"


# ── resolve_answer_text's category-tagged bypass (Setup Wizard, Phase 2) ───────────────────


def test_resolve_answer_text_returns_a_category_tagged_records_answer_verbatim() -> None:
    """A category-tagged record's ``answer`` is already a fully composed, self-contained
    sentence (``curator/elicitation.py::compose_elicitation_answer_text``, written at answer
    time) -- the generic picked-choice-label-plus-freeform concatenation below would lose that
    context (a bare label like ``"payments.revenue_amount"`` means nothing on its own), so a
    category-tagged record skips it entirely and returns ``answer`` untouched.
    """
    from governed_bi.curator.elicitation import compose_elicitation_answer_text
    from governed_bi.curator.clarifications import resolve_answer_text

    composed = compose_elicitation_answer_text(
        _record(
            category="A",
            choices=({"id": "payments.revenue_amount", "label": "payments.revenue_amount"},),
        ),
        choice_id="payments.revenue_amount",
    )
    rec = _record(
        category="A",
        answer_choice_id="payments.revenue_amount",
        choices=({"id": "payments.revenue_amount", "label": "payments.revenue_amount"},),
        answer=composed,
    )
    # Without the bypass this would double up as "payments.revenue_amount — 'revenue' maps to
    # payments.revenue_amount." -- the picked choice's own label glued onto the composed
    # sentence that already names it.
    assert resolve_answer_text(rec) == composed
    assert resolve_answer_text(rec) == "'orders' maps to payments.revenue_amount."


def test_resolve_answer_text_bypass_ignores_choices_entirely_for_a_category_tagged_record() -> None:
    from governed_bi.curator.clarifications import resolve_answer_text

    rec = _record(
        category="E",
        answer_choice_id="exclude",
        choices=({"id": "exclude", "label": "some label that must not appear"},),
        answer="orders.review_status: apply this exclusion by default.",
    )
    assert resolve_answer_text(rec) == "orders.review_status: apply this exclusion by default."


# ── append_if_new_scope (Setup Wizard, Phase 2): idempotent-by-scope ledger append ──────────


def test_append_if_new_scope_appends_and_returns_the_record(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import append_if_new_scope, load_clarifications

    record = _record(id="q001", scope="elicitation:join:orders:payments")
    appended = append_if_new_scope(tmp_path, record)
    assert appended == record
    (on_disk,) = load_clarifications(tmp_path)
    assert on_disk == record


def test_append_if_new_scope_is_a_no_op_when_the_scope_already_exists(tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import (
        append_if_new_scope,
        load_clarifications,
        write_clarifications,
    )

    write_clarifications(tmp_path, [_record(id="q001", scope="elicitation:join:orders:payments")])
    second = _record(id="q002", scope="elicitation:join:orders:payments")
    appended = append_if_new_scope(tmp_path, second)

    assert appended is None
    records = load_clarifications(tmp_path)
    assert len(records) == 1, "a second record with the same scope must not have been written"
    assert records[0].id == "q001"
