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
    reliability: Any = None,
) -> Any:
    from governed_bi.corpus.schema import ColumnAsset, Reliability

    return ColumnAsset(
        id=f"{table_id}.{name}",
        schema="shop",
        parent_table=table_id,
        physical_name=name,
        summary=name,
        logical_type=logical_type,
        physical_type=physical_type,
        sample_values=tuple(samples),
        reliability=reliability or Reliability(),
    )


def _schema(*, suspect: bool = False) -> tuple[list[Any], dict[str, Any]]:
    """v1's ``_schema_tables()`` fixture, ported onto v2's split table/column assets.

    ``sample_values`` is still populated on the two value-gated columns **on purpose**: it is
    the field B and E used to read and no longer do (it is empty on every live-seeded corpus),
    so leaving it here is what lets a test assert those categories stay silent when the
    governed value-reading path returns nothing.

    ``suspect`` flags the same two columns ``ReliabilityStatus.suspect``, which is what
    ``check()``'s COLUMNS layer refuses under ``hard_block_suspect``.
    """
    from governed_bi.corpus.schema import LogicalType, Reliability, ReliabilityStatus, TableAsset

    reliability = Reliability(status=ReliabilityStatus.suspect) if suspect else Reliability()
    orders_columns = [
        _column("shop.orders", "order_id"),
        _column("shop.orders", "order_date", logical_type=LogicalType.date),
        _column("shop.orders", "total_amount", logical_type=LogicalType.decimal),
        _column(
            "shop.orders", "country_code",
            samples=("US", "CA", "MX", "FR", "DE"), reliability=reliability,
        ),
        _column(
            "shop.orders", "review_status",
            samples=("approved", "pending", "not_yet_rated"), reliability=reliability,
        ),
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


class _ScriptedConnector:
    """This repo's governed-query test idiom — ``tests/serve/test_agent_tools_hitl.py``'s
    ``Recorder``: a ``dialect`` and an ``execute`` returning ``(columns, rows, truncated)``,
    and nothing else. ``ports.Connector.sample_values`` no longer exists to fake.

    Keyed on the quoted column identifier the governed statement carries, so one connector
    answers for a whole schema and a column nobody scripted returns no rows.
    """

    dialect = "postgres"

    def __init__(self, by_column: dict[str, tuple[str, ...]]) -> None:
        self.by_column = by_column
        self.statements: list[str] = []

    def execute(self, sql: str, **_kwargs: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        self.statements.append(sql)
        for name, values in self.by_column.items():
            if f'"{name}"' in sql:
                return ([name], [(v,) for v in values], False)
        return ([], [], False)


def _observed(
    tables: list[Any],
    assets_by_id: dict[str, Any],
    *,
    connector: Any,
    suspect_blocked: bool = False,
    max_reads: int | None = None,
) -> tuple[dict[str, tuple[str, ...]], tuple[Any, ...]]:
    """``read_observed_values`` over a real ``AnalystCorpus``, as the route calls it."""
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.curator.elicitation import MAX_VALUE_READS, read_observed_values
    from governed_bi.govern.policy import GovernancePolicy

    return read_observed_values(
        tables,
        assets_by_id,
        connector=connector,
        corpus=for_analyst(list(assets_by_id.values())),
        policy=GovernancePolicy(hard_block_suspect=suspect_blocked),
        max_reads=MAX_VALUE_READS if max_reads is None else max_reads,
    )


#: What the two value-gated columns of :func:`_schema` really hold in the fake database.
_REAL_VALUES: dict[str, tuple[str, ...]] = {
    "country_code": ("US", "CA", "MX", "FR", "DE"),
    "review_status": ("approved", "pending", "not_yet_rated"),
}


# ── candidate generation ────────────────────────────────────────────────────────────────────


def test_generate_candidate_questions_is_category_tagged() -> None:
    from governed_bi.curator.clarifications import ClarificationRecordStatus
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    observed, _ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector(_REAL_VALUES)
    )
    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)
    assert records, "expected at least one candidate"
    for rec in records:
        assert rec.source == "elicitation_wizard"
        assert rec.category in {"A", "B", "C", "D", "E"}
        assert rec.status is ClarificationRecordStatus.open

    categories = {rec.category for rec in records}
    assert "A" in categories  # "revenue"/"amount"/"total" ambiguous terms found
    assert "C" in categories  # a date column exists -> fiscal-year-start rule
    assert "E" in categories  # review_status really holds a null-like sentinel
    assert "B" in categories  # country_code really is a small categorical column


def test_d_is_never_generated_as_a_standalone_candidate() -> None:
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    observed, _ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector(_REAL_VALUES)
    )
    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)
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
    observed, _ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector(_REAL_VALUES)
    )
    first = generate_candidate_questions(tables, assets_by_id, observed_values=observed)
    second = generate_candidate_questions(
        tables, assets_by_id, existing=first, observed_values=observed
    )
    assert second == []


def test_generate_respects_limit_per_category() -> None:
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    observed, _ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector(_REAL_VALUES)
    )
    records = generate_candidate_questions(
        tables, assets_by_id, limit_per_category=1, observed_values=observed
    )
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
    observed, _ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector(_REAL_VALUES)
    )
    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)
    ids = [r.id for r in records]
    assert all(ids)
    assert len(ids) == len(set(ids))


# ── B and E read real values through the governed sample path (serve/fetch.py) ──────────────
#
# The bug these pin: both categories gated on ``ColumnAsset.sample_values``, which
# ``corpus/seed.py``'s live-schema introspection never populates (0 of 5 947 columns in the
# obfuscated lake carry it), so neither could ever fire in production. The fix reads the values
# through ``serve/fetch.sample_rows`` — the same governed statement + ``prepare()`` + ledger row
# the live agent's own ``sample_rows`` tool takes — and not by restoring the deleted, unescaped,
# un-ledgered ``Connector.sample_values`` port method (``ports.py`` around line 124).


def test_b_offers_the_values_the_database_really_returned() -> None:
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    connector = _ScriptedConnector(_REAL_VALUES)
    observed, _ledger = _observed(tables, assets_by_id, connector=connector)
    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)

    (b_rec,) = [r for r in records if r.category == "B"]
    assert b_rec.target_table == "orders"
    assert b_rec.target_column == "country_code"
    assert [c["id"] for c in (b_rec.choices or ())] == ["CA", "DE", "FR", "MX", "US"]
    assert any('"country_code"' in sql for sql in connector.statements), connector.statements


def test_e_quotes_the_sentinel_the_database_really_returned() -> None:
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    observed, _ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector(_REAL_VALUES)
    )
    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)

    (e_rec,) = [r for r in records if r.category == "E"]
    assert e_rec.target_column == "review_status"
    # 'pending' is the one value of the three that is in ``_SENTINEL_VALUES``.
    assert "'pending'" in e_rec.question, e_rec.question


def test_b_and_e_stay_silent_when_no_values_were_read() -> None:
    """The regression pin for the original bug: ``sample_values`` is populated on both gated
    columns in the fixture, and it must no longer be what these two categories read."""
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    records = generate_candidate_questions(tables, assets_by_id, observed_values={})
    categories = {r.category for r in records}
    assert categories == {"A", "C"}, categories

    # And the same when the argument is simply not supplied at all.
    assert {r.category for r in generate_candidate_questions(tables, assets_by_id)} == {"A", "C"}


def test_b_and_e_stay_silent_when_the_column_returns_no_rows() -> None:
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    observed, ledger = _observed(tables, assets_by_id, connector=_ScriptedConnector({}))
    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)

    assert {r.category for r in records} == {"A", "C"}
    assert observed == {"shop.orders.country_code": (), "shop.orders.review_status": ()}
    assert [row["passed"] for row in ledger] == [True, True], "the statements did run"


def test_a_governance_refusal_skips_the_column_instead_of_bypassing_it() -> None:
    """A ``suspect``-flagged column under ``hard_block_suspect`` is refused at COLUMNS. The
    right answer is "not a candidate", never "read it some other way": nothing reaches the
    engine, the refusal still gets its ledger row, and B/E propose nothing for it."""
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema(suspect=True)
    connector = _ScriptedConnector(_REAL_VALUES)
    observed, ledger = _observed(
        tables, assets_by_id, connector=connector, suspect_blocked=True
    )

    assert observed == {}
    assert not connector.statements, f"a refused column's values were read: {connector.statements}"
    assert [row["reason_code"] for row in ledger] == ["r_column_suspect", "r_column_suspect"]
    assert all(row["passed"] is False and row["executed_sql"] is None for row in ledger)

    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)
    assert {r.category for r in records} == {"A", "C"}


def test_every_governed_read_gets_its_own_ledger_row() -> None:
    """One ``path="sample"`` row per statement — the whole reason for using this path rather
    than the deleted one, which reached the database through no layer and wrote no row."""
    tables, assets_by_id = _schema()
    _observed_values, ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector(_REAL_VALUES)
    )

    assert len(ledger) == 2, "one row per value-gated column, no more and no fewer"
    assert all(row["path"] == "sample" for row in ledger), ledger
    assert all(row["passed"] for row in ledger), ledger


def test_only_the_columns_b_or_e_could_want_are_ever_read() -> None:
    """Cost: ``POST /elicitation/generate`` issues one governed query per *gated* column, not
    per column. ``order_id``/``order_date``/``total_amount``/``payment_id``/``revenue_amount``
    match neither keyword list, so no statement is built for them."""
    tables, assets_by_id = _schema()
    connector = _ScriptedConnector(_REAL_VALUES)
    _observed(tables, assets_by_id, connector=connector)

    assert len(connector.statements) == 2, connector.statements
    assert not any("order_id" in sql for sql in connector.statements)


def test_the_number_of_governed_reads_per_call_is_bounded() -> None:
    """``max_reads`` is a ceiling on one admin click, not a knob a caller widens per column."""
    from governed_bi.curator.elicitation import MAX_VALUE_READS

    assert MAX_VALUE_READS == 50

    tables, assets_by_id = _schema()
    connector = _ScriptedConnector(_REAL_VALUES)
    observed, ledger = _observed(tables, assets_by_id, connector=connector, max_reads=1)

    assert len(connector.statements) == 1
    assert len(ledger) == 1
    assert set(observed) == {"shop.orders.country_code"}, "truncated in a fixed order"


def test_b_uses_the_governed_caps_headroom_to_decide_small_cardinality() -> None:
    """``_propose_b``'s ">1 and <=15 distinct" test survives the move to a capped read.

    ``distinct_values_statement`` returns at most ``SAMPLE_ROWS_MAX_VALUES`` (20) values, and
    20 is strictly above the 15 the predicate cares about — so a column at the cap is known to
    be *too big* rather than merely truncated, and the predicate stays exact rather than
    becoming an estimate.
    """
    from governed_bi.curator.elicitation import generate_candidate_questions
    from governed_bi.serve.fetch import SAMPLE_ROWS_MAX_VALUES

    assert SAMPLE_ROWS_MAX_VALUES > 15, "the headroom this reasoning depends on"

    for count, fires in ((2, True), (15, True), (16, False), (SAMPLE_ROWS_MAX_VALUES, False)):
        tables, assets_by_id = _schema()
        connector = _ScriptedConnector(
            {"country_code": tuple(f"C{i:02d}" for i in range(count))}
        )
        observed, _ledger = _observed(tables, assets_by_id, connector=connector)
        records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)
        assert any(r.category == "B" for r in records) is fires, (count, fires)


def test_a_single_value_column_is_not_worth_a_value_mapping_question() -> None:
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    observed, _ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector({"country_code": ("US",)})
    )
    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)
    assert not [r for r in records if r.category == "B"]


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
