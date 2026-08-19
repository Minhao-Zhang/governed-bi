"""curator/elicitation_terms.py: category A, the wizard's one hybrid gap.

The pair, not the keyword scan that finds its candidates — ``test_elicitation.py`` is where the
generator's other four categories live and where the gates that admit a column are pinned. What
is here is what makes A different from all of them: it resolves into **two** records with a
dependency between them, one per audience, and the business half's choices have to be grounded
in something measured rather than in what a plausible business meaning sounds like.

Its own two-table fixture rather than ``test_elicitation.py``'s, because the shape the pair needs
is specific and is the point: one term (``amount``) matching **two** columns on two tables, so
both halves are minted, and one (``revenue``) matching a single column, so the business half is
suppressed as the gap model's ``A″``. The language properties over the generated text are in
``test_wizard_phrasing.py``, with the guard whose asymmetry they are about.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


def _column(table_id: str, name: str, *, logical_type: Any = None) -> Any:
    from governed_bi.corpus.schema import ColumnAsset

    return ColumnAsset(
        id=f"{table_id}.{name}",
        schema="shop",
        parent_table=table_id,
        physical_name=name,
        summary=name,
        logical_type=logical_type,
    )


def _schema() -> tuple[list[Any], dict[str, Any]]:
    from governed_bi.corpus.schema import LogicalType, TableAsset

    orders_columns = [
        _column("shop.orders", "order_id"),
        _column("shop.orders", "order_date", logical_type=LogicalType.date),
        _column("shop.orders", "total_amount", logical_type=LogicalType.decimal),
        _column("shop.orders", "country_code"),
        _column("shop.orders", "review_status"),
    ]
    orders = TableAsset(
        id="shop.orders", schema="shop", physical_name="orders", summary="orders",
        columns=tuple(c.id for c in orders_columns),
    )
    payments_columns = [
        _column("shop.payments", "payment_id"),
        _column("shop.payments", "revenue_amount", logical_type=LogicalType.decimal),
    ]
    payments = TableAsset(
        id="shop.payments", schema="shop", physical_name="payments", summary="payments",
        columns=tuple(c.id for c in payments_columns),
    )
    tables = [orders, payments]
    return tables, {a.id: a for a in [orders, payments, *orders_columns, *payments_columns]}


# ── one gap, two audience-specific records ──────────────────────────────────────────────────



def test_an_ambiguous_term_becomes_an_ordered_pair_of_audience_specific_questions() -> None:
    """``detent-ai-setup-wizard-gap-model.md`` § "Which gap types produce two audience-specific
    questions", point 1. One gap, two records, one dependency: the business owner says which
    meaning, the DBA binds it to a column, and the second waits on the first.

    Both offer the same candidate set under the same ids — that is what lets
    ``apply_cluster_dependencies`` gate them on the same contested column and what lets the D
    join follow-up read a picked column off either — and they differ in the *language* of what
    the admin actually reads.
    """
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    records = generate_candidate_questions(tables, assets_by_id)
    by_scope = {r.scope: r for r in records}

    biz = by_scope["elicitation:term:amount"]
    eng = by_scope["elicitation:termcolumn:amount"]
    assert (biz.audience, eng.audience) == ("business", "data")
    assert eng.blocked_by == (biz.id,), "the engineering half waits on the business one"
    assert biz.blocked_by == (), "and nothing waits on the business one"
    assert biz.allow_freeform and eng.allow_freeform

    candidates = {"orders.total_amount", "payments.revenue_amount"}
    assert {c["id"] for c in biz.choices or ()} == candidates
    assert {c["id"] for c in eng.choices or ()} == candidates

    # Only the engineering half is a column picker, and only it carries the expected table the
    # D join heuristic compares a pick against ("orders", alphabetically first).
    assert (biz.ui_modality, eng.ui_modality) == (None, "column_picker")
    assert (biz.target_table, eng.target_table) == (None, "orders")


def test_a_term_with_one_candidate_column_asks_the_engineer_only() -> None:
    """The gap model's ``A″`` row: "a column picker with a single forced choice is not a
    disambiguation". Measured on ``bird_rootbeer_en``, where "cost" offered
    ``rootbeerbrand.WholesaleCost`` alone.

    It is the *business* half that goes. With one candidate there is no meaning to choose
    between, so the question would teach an admin to click the only button on screen. The
    engineering half stays — with one candidate it is a confirmation a DBA can give, and dropping
    it would delete a finding, which is a decision the owner's "list ALL gaps" already made.
    """
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    scopes = {r.scope for r in generate_candidate_questions(tables, assets_by_id)}

    # "revenue" matches ``payments.revenue_amount`` and nothing else.
    assert "elicitation:termcolumn:revenue" in scopes
    assert "elicitation:term:revenue" not in scopes
    # ...while "amount" matches two columns and keeps both halves.
    assert {"elicitation:term:amount", "elicitation:termcolumn:amount"} <= scopes


def test_a_cluster_edge_is_added_to_the_pair_edge_and_never_replaces_it() -> None:
    """``apply_cluster_dependencies`` overwrote ``blocked_by`` until the A pair landed, which
    would have silently deleted A-eng's warrant for exactly the A questions that also name a
    contested column — the ones with the most reason to wait."""
    from governed_bi.curator.elicitation import generate_candidate_questions
    from governed_bi.curator.gaps import apply_cluster_dependencies

    tables, assets_by_id = _schema()
    records = generate_candidate_questions(tables, assets_by_id)
    biz = next(r for r in records if r.scope == "elicitation:term:amount")

    gated = apply_cluster_dependencies(records, {"orders.total_amount": "elicit.cluster"})
    by_scope = {rec.scope: rec for rec in gated}
    assert by_scope["elicitation:termcolumn:amount"].blocked_by == ("elicit.cluster", biz.id)
    # A-biz offers the same contested column and picks up the cluster edge on its own account.
    assert by_scope["elicitation:term:amount"].blocked_by == ("elicit.cluster",)


# ── carrying an A-biz answer across to the A-eng question waiting on it ─────────────────────


def _answered_biz(**overrides: Any) -> Any:
    from governed_bi.curator.clarifications import ClarificationRecord, ClarificationRecordStatus

    defaults: dict[str, Any] = dict(
        id="q_biz",
        scope="elicitation:term:amount",
        question="When someone in your business asks about 'amount', which of these do they mean?",
        status=ClarificationRecordStatus.answered,
        category="A",
        choices=(
            {"id": "orders.total_amount", "label": "the 'total amount' recorded in your orders data"},
        ),
        answer_choice_id="orders.total_amount",
        answer="In business terms, 'amount' means the 'total amount' recorded in your orders data.",
        source="elicitation_wizard",
    )
    defaults.update(overrides)
    return ClarificationRecord(**defaults)


def _open_eng(**overrides: Any) -> Any:
    from governed_bi.curator.clarifications import ClarificationRecord

    defaults: dict[str, Any] = dict(
        id="q_eng",
        scope="elicitation:termcolumn:amount",
        question="Which column holds 'amount'?",
        category="A",
        blocked_by=("q_biz",),
        source="elicitation_wizard",
    )
    defaults.update(overrides)
    return ClarificationRecord(**defaults)


def test_an_answered_business_definition_is_quoted_into_the_engineering_question() -> None:
    """The doc's A-eng frame — *"Business defines revenue as '…'. Which column holds that?"* —
    which can only be written once the business half is answered, while the record itself has to
    exist from scan time so a DBA with no business counterpart can still answer it. So the
    question is *restated*, not minted.

    The whole picked label is quoted, counts and all: those counts were measured on this
    database and are exactly what a DBA can check a candidate column against.
    """
    from governed_bi.curator.elicitation_terms import restate_with_business_definition

    biz, eng = _answered_biz(), _open_eng()
    restated = restate_with_business_definition(biz, [biz, eng])
    assert restated == (
        "q_eng",
        "Business defines 'amount' as \"the 'total amount' recorded in your orders data\". "
        "Which column holds that?",
    )


def test_a_freeform_business_definition_is_quoted_in_the_admins_own_words() -> None:
    """The raw free text comes from the caller, because the record no longer holds it: the answer
    route overwrites ``answer`` with the composed corpus sentence. Reading that back produced,
    live on real ``app_store``, the corpus frame nested inside the question frame —
    ``Business defines 'price' as "In business terms, 'price' means what a shopper pays…"``.
    """
    from governed_bi.curator.elicitation_terms import restate_with_business_definition

    biz = _answered_biz(answer_choice_id=None, answer="In business terms, 'amount' means takings.")
    eng = _open_eng()
    (_id, question) = restate_with_business_definition(biz, [biz, eng], freeform="takings")
    assert question == "Business defines 'amount' as 'takings'. Which column holds that?"
    assert "In business terms" not in question, "the corpus frame must not be quoted back"


def test_a_pick_and_free_text_are_both_quoted() -> None:
    """The same two halves, joined the same way the corpus fact joins them: they are one answer
    written for two readers."""
    from governed_bi.curator.elicitation_terms import restate_with_business_definition

    biz, eng = _answered_biz(), _open_eng()
    (_id, question) = restate_with_business_definition(biz, [biz, eng], freeform="before refunds")
    assert question == (
        "Business defines 'amount' as \"the 'total amount' recorded in your orders data; "
        "before refunds\". Which column holds that?"
    )


def test_nothing_is_restated_for_a_half_that_is_already_answered() -> None:
    """An answered A-eng has already folded a corpus fact whose asset id is a hash of its
    question text (``draft_from_clarification``). Rewriting the question would strand that fact
    where ``candidate_rules.drop_already_answered`` can no longer match it, and the wizard would
    ask again."""
    from governed_bi.curator.clarifications import ClarificationRecordStatus
    from governed_bi.curator.elicitation_terms import restate_with_business_definition

    biz = _answered_biz()
    eng = _open_eng(status=ClarificationRecordStatus.answered)
    assert restate_with_business_definition(biz, [biz, eng]) is None


def test_nothing_is_restated_by_an_answer_that_is_not_a_business_definition() -> None:
    from governed_bi.curator.elicitation_terms import restate_with_business_definition

    eng = _open_eng()
    assert restate_with_business_definition(_answered_biz(scope="elicitation:valuemap:o.c"), [eng]) is None
    assert restate_with_business_definition(_answered_biz(), [_open_eng(scope="x")]) is None
