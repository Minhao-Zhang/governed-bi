"""curator/elicitation.py: the Phase 1 Setup Wizard heuristic candidate generator (UtkuAI v1,
ported), category-aware answer composition, and the D join-path auto-follow-up.

Ported intent of v1's ``governed-bi-v1-demo/tests/test_elicitation.py``, adapted to this repo's
actual asset shape: v1's ``TableAsset.columns`` is a list of inline ``Column`` objects; v2's
``TableAsset.columns`` is a tuple of **column ids** (``corpus/schema.py``), with each
``ColumnAsset`` its own entry in ``assets_by_id`` — so every generator function here takes
``assets_by_id`` alongside ``tables`` and resolves columns through it, the same way
``api/browse_routes.py`` already does for every other table/column walk in this codebase.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


def _column(
    table_id: str,
    name: str,
    *,
    logical_type: Any = None,
    physical_type: str | None = None,
    samples: tuple[Any, ...] = (),
) -> Any:
    from governed_bi.corpus.schema import ColumnAsset

    return ColumnAsset(
        id=f"{table_id}.{name}",
        schema="shop",
        parent_table=table_id,
        physical_name=name,
        summary=name,
        logical_type=logical_type,
        physical_type=physical_type,
        sample_values=tuple(samples),
    )


def _schema() -> tuple[list[Any], dict[str, Any]]:
    """v1's ``_schema_tables()`` fixture, ported onto v2's split table/column assets."""
    from governed_bi.corpus.schema import LogicalType, TableAsset

    orders_columns = [
        _column("shop.orders", "order_id"),
        _column("shop.orders", "order_date", logical_type=LogicalType.date),
        _column("shop.orders", "total_amount", logical_type=LogicalType.decimal),
        _column("shop.orders", "country_code", samples=("US", "CA", "MX", "FR", "DE")),
        _column("shop.orders", "review_status", samples=("approved", "pending", "not_yet_rated")),
    ]
    orders = TableAsset(
        id="shop.orders",
        schema="shop",
        physical_name="orders",
        summary="orders",
        columns=tuple(c.id for c in orders_columns),
    )
    payments_columns = [
        _column("shop.payments", "payment_id"),
        _column("shop.payments", "revenue_amount", logical_type=LogicalType.decimal),
    ]
    payments = TableAsset(
        id="shop.payments",
        schema="shop",
        physical_name="payments",
        summary="payments",
        columns=tuple(c.id for c in payments_columns),
    )
    tables = [orders, payments]
    assets_by_id = {a.id: a for a in [orders, payments, *orders_columns, *payments_columns]}
    return tables, assets_by_id


# ── candidate generation ────────────────────────────────────────────────────────────────────


def test_generate_candidate_questions_is_category_tagged() -> None:
    from governed_bi.curator.clarifications import ClarificationRecordStatus
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    records = generate_candidate_questions(tables, assets_by_id)
    assert records, "expected at least one candidate"
    for rec in records:
        assert rec.source == "elicitation_wizard"
        assert rec.category in {"A", "B", "C", "D", "E"}
        assert rec.status is ClarificationRecordStatus.open

    categories = {rec.category for rec in records}
    assert "A" in categories  # "revenue"/"amount"/"total" ambiguous terms found
    assert "C" in categories  # a date column exists -> fiscal-year-start rule
    assert "E" in categories  # review_status has a "not_yet_rated"-style sentinel
    assert "B" in categories  # country_code is a small categorical column


def test_d_is_never_generated_as_a_standalone_candidate() -> None:
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    records = generate_candidate_questions(tables, assets_by_id)
    assert all(rec.category != "D" for rec in records)


def test_a_question_offers_column_picker_choices_across_tables() -> None:
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    records = generate_candidate_questions(tables, assets_by_id)
    revenue_like = [r for r in records if r.category == "A" and "amount" in r.scope]
    assert revenue_like, "expected an A question for the 'amount' term"
    rec = revenue_like[0]
    assert rec.ui_modality == "column_picker"
    assert rec.allow_freeform is True
    labels = {c["id"] for c in (rec.choices or [])}
    assert "orders.total_amount" in labels
    assert "payments.revenue_amount" in labels
    # target_table is the alphabetically-first matching table ("orders").
    assert rec.target_table == "orders"


def test_generate_is_idempotent_against_existing_ledger() -> None:
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    first = generate_candidate_questions(tables, assets_by_id)
    second = generate_candidate_questions(tables, assets_by_id, existing=first)
    assert second == []


def test_generate_respects_limit_per_category() -> None:
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    records = generate_candidate_questions(tables, assets_by_id, limit_per_category=1)
    for category in ("A", "B", "C", "E"):
        assert len([r for r in records if r.category == category]) <= 1


def test_c_fires_from_physical_type_when_logical_type_is_unset() -> None:
    """Real-corpus gap, found live against ``beer_factory``: ``corpus/seed.py``'s live-schema
    introspection never populates ``ColumnAsset.logical_type`` (v1's own ``Column`` always had
    it set) -- only ``physical_type`` (the raw DB type string, e.g. ``"date"``). Without this
    fallback, C never fires against any freshly-seeded, uncurated corpus at all.
    """
    from governed_bi.corpus.schema import TableAsset
    from governed_bi.curator.elicitation import generate_candidate_questions

    cols = [_column("s.t", "order_id"), _column("s.t", "order_date", physical_type="date")]
    table = TableAsset(id="s.t", schema="s", physical_name="t", summary="t", columns=tuple(c.id for c in cols))
    assets_by_id = {a.id: a for a in [table, *cols]}

    records = generate_candidate_questions([table], assets_by_id)
    assert any(r.category == "C" for r in records)


def test_generated_ids_are_unique_and_non_empty() -> None:
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    records = generate_candidate_questions(tables, assets_by_id)
    ids = [r.id for r in records]
    assert all(ids)
    assert len(ids) == len(set(ids))


# ── D auto-follow-up (never standalone; only tied to an A answer) ──────────────────────────


def test_join_followup_none_when_picked_table_matches_expected() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import maybe_generate_join_followup

    rec = ClarificationRecord(
        id="q001",
        scope="elicitation:term:amount",
        question="When you say 'amount', which table/column does that map to?",
        category="A",
        ui_modality="column_picker",
        choices=({"id": "orders.total_amount", "label": "orders.total_amount"},),
        allow_freeform=False,
        target_table="orders",
        source="elicitation_wizard",
    )
    assert maybe_generate_join_followup(rec, "orders.total_amount") is None


def test_join_followup_generated_when_picked_table_differs() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord, ClarificationRecordStatus
    from governed_bi.curator.elicitation import maybe_generate_join_followup

    rec = ClarificationRecord(
        id="q001",
        scope="elicitation:term:amount",
        question="When you say 'amount', which table/column does that map to?",
        category="A",
        ui_modality="column_picker",
        choices=(
            {"id": "orders.total_amount", "label": "orders.total_amount"},
            {"id": "payments.revenue_amount", "label": "payments.revenue_amount"},
        ),
        allow_freeform=False,
        target_table="orders",
        source="elicitation_wizard",
    )
    followup = maybe_generate_join_followup(rec, "payments.revenue_amount")
    assert followup is not None
    assert followup.category == "D"
    assert followup.status is ClarificationRecordStatus.open
    assert followup.target_table == "payments"
    assert followup.target_column == "revenue_amount"
    assert "orders" in followup.question and "payments" in followup.question


def test_join_followup_none_for_a_non_a_category_record() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import maybe_generate_join_followup

    rec = ClarificationRecord(id="q001", scope="s", question="q?", category="E", target_table="orders")
    assert maybe_generate_join_followup(rec, "payments.revenue_amount") is None


# ── category priority order (A > C > E > B > D) ─────────────────────────────────────────────


def test_category_priority_order() -> None:
    from governed_bi.curator.elicitation import CATEGORY_PRIORITY

    assert CATEGORY_PRIORITY == ["A", "C", "E", "B", "D"]


# ── answer composition: choice-picked ───────────────────────────────────────────────────────


def test_compose_answer_text_category_a() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import compose_elicitation_answer_text

    rec = ClarificationRecord(
        id="q001",
        scope="elicitation:term:revenue",
        question="?",
        category="A",
        choices=({"id": "payments.revenue_amount", "label": "payments.revenue_amount"},),
        source="elicitation_wizard",
    )
    text = compose_elicitation_answer_text(rec, choice_id="payments.revenue_amount")
    assert text == "'revenue' maps to payments.revenue_amount."


def test_compose_answer_text_category_c() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import compose_elicitation_answer_text

    rec = ClarificationRecord(
        id="q002", scope="elicitation:rule:fiscal_year_start", question="?", category="C",
        source="elicitation_wizard",
    )
    assert compose_elicitation_answer_text(rec, freeform="4") == "Fiscal year starts in month 4."
    assert compose_elicitation_answer_text(rec, freeform="") == ""


def test_compose_answer_text_category_e_exclude_and_include() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import compose_elicitation_answer_text

    rec = ClarificationRecord(
        id="q003",
        scope="elicitation:exclusion:orders.review_status",
        question="?",
        category="E",
        choices=(
            {"id": "exclude", "label": "Exclude rows where review_status = 'not_yet_rated'"},
            {"id": "include", "label": "Include them"},
        ),
        target_table="orders",
        target_column="review_status",
        source="elicitation_wizard",
    )
    excluded = compose_elicitation_answer_text(rec, choice_id="exclude")
    assert "apply this exclusion by default" in excluded
    included = compose_elicitation_answer_text(rec, choice_id="include")
    assert "no default exclusion" in included


def test_compose_answer_text_category_b_checklist() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import compose_elicitation_answer_text

    rec = ClarificationRecord(
        id="q004",
        scope="elicitation:valuemap:orders.country_code",
        question="?",
        category="B",
        choices=tuple({"id": v, "label": v} for v in ["US", "CA", "MX"]),
        target_table="orders",
        target_column="country_code",
        source="elicitation_wizard",
    )
    text = compose_elicitation_answer_text(rec, choice_ids=["US", "CA"])
    assert "US, CA" in text
    assert compose_elicitation_answer_text(rec, choice_ids=[]) == ""


# ── answer composition: freeform (the "choice-picked answer disappears" bug class, for the
# opposite input shape -- every category must also accept a user answering in their own words
# without silently losing that answer) ──────────────────────────────────────────────────────


def test_compose_answer_text_category_a_via_freeform() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import compose_elicitation_answer_text

    rec = ClarificationRecord(
        id="q001",
        scope="elicitation:term:revenue",
        question="?",
        category="A",
        choices=({"id": "payments.revenue_amount", "label": "payments.revenue_amount"},),
        source="elicitation_wizard",
    )
    text = compose_elicitation_answer_text(rec, freeform="orders.grand_total")
    assert text == "'revenue' maps to orders.grand_total."


def test_compose_answer_text_category_c_via_choice() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import compose_elicitation_answer_text

    rec = ClarificationRecord(
        id="q002",
        scope="elicitation:rule:fiscal_year_start",
        question="?",
        category="C",
        choices=({"id": "10", "label": "10 - October"},),
        source="elicitation_wizard",
    )
    text = compose_elicitation_answer_text(rec, choice_id="10")
    assert text == "Fiscal year starts in month 10 - October."


def test_compose_answer_text_category_e_via_freeform() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import compose_elicitation_answer_text

    rec = ClarificationRecord(
        id="q003",
        scope="elicitation:exclusion:orders.review_status",
        question="?",
        category="E",
        choices=(
            {"id": "exclude", "label": "Exclude rows where review_status = 'not_yet_rated'"},
            {"id": "include", "label": "Include them"},
        ),
        target_table="orders",
        target_column="review_status",
        source="elicitation_wizard",
    )
    text = compose_elicitation_answer_text(rec, freeform="Only exclude when the reviewer was a bot")
    assert "orders.review_status" in text
    assert "Only exclude when the reviewer was a bot" in text


def test_compose_answer_text_category_b_via_freeform() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import compose_elicitation_answer_text

    rec = ClarificationRecord(
        id="q004",
        scope="elicitation:valuemap:orders.country_code",
        question="?",
        category="B",
        choices=tuple({"id": v, "label": v} for v in ["US", "CA", "MX"]),
        target_table="orders",
        target_column="country_code",
        source="elicitation_wizard",
    )
    text = compose_elicitation_answer_text(rec, freeform="Anything in North America")
    assert "orders.country_code" in text
    assert "Anything in North America" in text


def test_compose_answer_text_category_d_is_always_freeform() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import compose_elicitation_answer_text

    rec = ClarificationRecord(id="q005", scope="elicitation:join:orders:payments", question="?", category="D")
    assert compose_elicitation_answer_text(rec, freeform="orders.id = payments.order_id") == (
        "orders.id = payments.order_id"
    )
