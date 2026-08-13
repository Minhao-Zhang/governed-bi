"""curator/elicitation.py: the Phase 1 Setup Wizard heuristic candidate generator (UtkuAI v1,
ported) and the D join-path auto-follow-up. Answer composition moved with its module to
``tests/curator/test_elicitation_answers.py``.

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


#: Value-gated columns for :func:`_uncapped_schema`, all four of each kind past the retired cap
#: of 3. The B columns hold a small closed vocabulary; the E columns hold a null-like sentinel.
_UNCAPPED_VALUES: dict[str, tuple[str, ...]] = {
    "country_code": ("US", "CA"), "region_type": ("north", "south"),
    "channel_code": ("web", "shop"), "segment_category": ("smb", "ent"),
    "order_status": ("n/a", "shipped"), "user_rating": ("n/a", "5"),
    "quality_grade": ("n/a", "A"), "shipment_state": ("n/a", "in_transit"),
}


def _uncapped_schema() -> tuple[list[Any], dict[str, Any]]:
    """One table whose keyword gates match **more than three** candidates in three categories.

    :func:`_schema` cannot show a cap being gone: it matches exactly three ambiguous terms and
    one column each for B and E, so it produces the same output capped or not. This fixture is
    deliberately past the old ``limit_per_category=3`` in A (six terms), B (four columns) and E
    (four columns), which is what lets the test below fail if a quota ever comes back.
    """
    from governed_bi.corpus.schema import TableAsset

    names = ("revenue_total", "unit_price", "cost_basis", "account_balance", "book_value",
             *_UNCAPPED_VALUES)
    columns = [_column("shop.wide", n, physical_type="text") for n in names]
    table = TableAsset(
        id="shop.wide", schema="shop", physical_name="wide", summary="wide",
        columns=tuple(c.id for c in columns),
    )
    return [table], {a.id: a for a in [table, *columns]}


def test_no_category_is_capped_because_a_quota_drops_findings_silently() -> None:
    """The contract that replaced ``limit_per_category=3``: caps bound **cost**, never findings.

    The old test asserted the opposite — that a category stops at its quota — and a quota is the
    mechanism that makes a T1 finding vanish because three T3s were generated first, with nothing
    downstream able to tell that it happened (``curator/gaps.py``'s module docstring records the
    owner's 2026-08-12 decision: list ALL gaps, stratify by severity instead).

    So the assertion is that the *whole* gate output is reported: every ambiguous term matching a
    column gets its A question, and every value-gated column clearing its window gets its B or E
    one. Each of those three counts is above the retired cap on this fixture.
    """
    import pytest

    from governed_bi.curator.elicitation import _AMBIGUOUS_TERMS, generate_candidate_questions

    tables, assets_by_id = _schema()
    with pytest.raises(TypeError):
        # The cap is gone from the signature too, not merely defaulted to something large.
        generate_candidate_questions(tables, assets_by_id, limit_per_category=1)

    tables, assets_by_id = _uncapped_schema()
    observed, _ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector(_UNCAPPED_VALUES)
    )
    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)
    by_category: dict[str, set[str]] = {}
    for rec in records:
        by_category.setdefault(rec.category or "", set()).add(rec.scope.rsplit(":", 1)[-1])

    names = [c.physical_name for c in assets_by_id.values() if hasattr(c, "parent_table")]
    assert by_category["A"] == {t for t in _AMBIGUOUS_TERMS if any(t in n for n in names)}
    assert len(by_category["A"]) == 6, by_category["A"]
    assert len(by_category["B"]) == 4, by_category["B"]
    assert len(by_category["E"]) == 4, by_category["E"]


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
    assert set(observed) == {c.id for c in assets_by_id.values() if hasattr(c, "parent_table")}
    assert set(observed.values()) == {()}
    assert [row["passed"] for row in ledger] == [True] * len(observed), "the statements did run"


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

    assert set(observed) == {
        "shop.orders.order_id", "shop.orders.order_date", "shop.orders.total_amount",
        "shop.payments.payment_id", "shop.payments.revenue_amount",
    }, "the two suspect columns, and only those, are missing"
    assert not any(
        "country_code" in sql or "review_status" in sql for sql in connector.statements
    ), f"a refused column's values were read: {connector.statements}"
    refusals = [row for row in ledger if not row["passed"]]
    assert [row["reason_code"] for row in refusals] == ["r_column_suspect", "r_column_suspect"]
    assert all(row["executed_sql"] is None for row in refusals)

    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)
    assert {r.category for r in records} == {"A", "C"}


def test_every_governed_read_gets_its_own_ledger_row() -> None:
    """One ``path="sample"`` row per statement — the whole reason for using this path rather
    than the deleted one, which reached the database through no layer and wrote no row."""
    tables, assets_by_id = _schema()
    _observed_values, ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector(_REAL_VALUES)
    )

    assert len(ledger) == 7, "one row per column read, no more and no fewer"
    assert all(row["path"] == "sample" for row in ledger), ledger
    assert all(row["passed"] for row in ledger), ledger


def test_every_column_is_read_now_that_a_value_driven_detector_exists() -> None:
    """The keyword gate on the *read set* is gone, and the cost change is the point rather than
    a side effect.

    It was defensible while every reader of the values was itself name-gated. ``_propose_s6`` is
    not: the design doc's S6 row exists because ``restaurant.geografisch.region = 'unknown'`` is
    missed for want of the name ``region`` being in a list, and a value-driven detector over a
    keyword-gated read set is still keyword-gated one layer down.
    """
    tables, assets_by_id = _schema()
    connector = _ScriptedConnector(_REAL_VALUES)
    _observed(tables, assets_by_id, connector=connector)

    assert len(connector.statements) == 7, connector.statements
    assert any("order_id" in sql for sql in connector.statements)


def test_the_number_of_governed_reads_per_call_is_bounded() -> None:
    """``max_reads`` is a ceiling on one admin click, not a knob a caller widens per column.

    Raised from 50 with the gate removal: at 50, "every column" would silently truncate
    ``beer_factory`` at 50 of 93, and a cost bound that deletes findings is the retired
    ``limit_per_category`` wearing a different hat. 800 clears the widest schema in the lake
    (``works_cycles``, 703 columns), so it bounds without binding.
    """
    from governed_bi.curator.elicitation import MAX_VALUE_READS

    assert MAX_VALUE_READS == 800
    assert MAX_VALUE_READS > 703, "the widest schema in the lake must not be truncated"

    tables, assets_by_id = _schema()
    connector = _ScriptedConnector(_REAL_VALUES)
    observed, ledger = _observed(tables, assets_by_id, connector=connector, max_reads=1)

    assert len(connector.statements) == 1
    assert len(ledger) == 1
    assert set(observed) == {"shop.orders.order_id"}, "truncated in a fixed order"


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


# ── severity / audience classification (utku-ai-setup-wizard-gap-model.md § "Gap-type ×
# severity × audience table") ────────────────────────────────────────────────────────────────


def test_every_generated_candidate_carries_a_severity_and_an_audience() -> None:
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    observed, _ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector(_REAL_VALUES)
    )
    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)
    assert records
    for rec in records:
        assert rec.severity in {"T1", "T2", "T3", "T4"}, rec
        assert rec.audience in {"business", "data"}, rec


def test_the_four_standalone_categories_carry_the_designed_classification() -> None:
    """A is ``data`` because the question as shipped *is* the engineering half of the doc's
    hybrid pair — its choices are bare ``table.column`` identifiers. B, C and E are ``business``:
    each carries a machine-prepared payload precisely so a domain owner never types a value."""
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    observed, _ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector(_REAL_VALUES)
    )
    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)
    by_category = {rec.category: rec for rec in records}

    assert (by_category["A"].severity, by_category["A"].audience) == ("T2", "data")
    assert (by_category["B"].severity, by_category["B"].audience) == ("T2", "business")
    assert (by_category["C"].severity, by_category["C"].audience) == ("T2", "business")
    assert (by_category["E"].severity, by_category["E"].audience) == ("T2", "business")


def test_the_d_join_followup_is_a_safe_failure_for_the_data_audience() -> None:
    """The shipped D record is the doc's **D′** row, not its D row: it fires when a join is not
    declared at all, so what an unanswered one costs is a refusal (T3), never a wrong number.
    The T1 D row — two candidate keys whose values disagree — has no detector yet."""
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import maybe_generate_join_followup

    rec = ClarificationRecord(
        id="q001",
        scope="elicitation:term:amount",
        question="?",
        category="A",
        target_table="orders",
        source="elicitation_wizard",
    )
    followup = maybe_generate_join_followup(rec, "payments.revenue_amount")
    assert followup is not None
    assert (followup.severity, followup.audience) == ("T3", "data")


def test_the_classification_table_covers_every_category_the_generator_can_emit() -> None:
    from governed_bi.curator.elicitation import CATEGORY_CLASSIFICATION, CATEGORY_PRIORITY

    assert set(CATEGORY_CLASSIFICATION) == set(CATEGORY_PRIORITY)


def test_the_keyword_generator_emits_unblocked_records_and_the_gate_is_applied_after() -> None:
    """The division of labour ``POST /elicitation/generate`` composes, pinned on both halves.

    This generator computes nothing about near-duplicate columns — it cannot, it reads a word
    list — so every record it returns is unblocked, and that is correct rather than a gap. The
    prerequisite is stamped by ``curator/gaps.py::apply_cluster_dependencies`` from the structural
    scan's contested-column map, which the route runs over this output before writing it. Where
    that map *comes from* is ``tests/curator/test_gaps.py``'s subject; that it lands on the right
    records here is this one's.

    Previously this test asserted only the first half, and was true because nothing called the
    second half at all.
    """
    from governed_bi.curator.elicitation import generate_candidate_questions
    from governed_bi.curator.gaps import apply_cluster_dependencies

    tables, assets_by_id = _schema()
    observed, _ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector(_REAL_VALUES)
    )
    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)
    assert all(rec.blocked_by == () for rec in records)

    gated = apply_cluster_dependencies(records, {"orders.country_code": "elicit.cluster"})
    by_category = {rec.category: rec for rec in gated}
    assert by_category["B"].target_column == "country_code"
    assert by_category["B"].blocked_by == ("elicit.cluster",)
    # A ranges over columns via its choices, so it waits on a cluster it merely offers as an
    # option; C names no column at all and can never be gated.
    assert by_category["C"].blocked_by == ()
    assert by_category["E"].blocked_by == ()


# ── S6: sentinel detection with no name gate (utku-ai-setup-wizard-gap-model.md § S6) ────────


def test_s6_finds_a_sentinel_in_a_column_no_keyword_list_names() -> None:
    """The design doc's own headline miss, reproduced on its real shape:
    ``restaurant.geografisch.region`` holds ``'unknown'`` on 17 of 168 rows and E never proposes
    it, because ``region`` is in ``_CATEGORICAL_HINTS`` and not in ``_STATUS_HINTS`` -- "E's real
    signal is the value, not the name; ANDing the two destroys recall".
    """
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _region_schema()
    observed = {"geo.geografisch.region": ("bay area", "los angeles", "napa valley", "unknown")}
    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)

    (s6,) = [r for r in records if r.scope.startswith("elicitation:sentinel:")]
    assert (s6.category, s6.severity, s6.audience) == ("E", "T2", "business")
    assert (s6.target_table, s6.target_column) == ("geografisch", "region")
    assert "'unknown'" in s6.question, s6.question
    assert {c["id"] for c in s6.choices or ()} == {"exclude", "include"}


def test_s6_does_not_ask_again_about_a_column_e_already_covered() -> None:
    """The two doc rows are one question with two provenances. Measured on ``app_store``, where
    all three ``*content_rating*`` columns hold ``'Unrated'`` and E's name gate reaches every
    one of them: S6 must add nothing there rather than double every card."""
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _schema()
    observed, _ledger = _observed(
        tables, assets_by_id, connector=_ScriptedConnector(_REAL_VALUES)
    )
    records = generate_candidate_questions(tables, assets_by_id, observed_values=observed)

    exclusions = [r for r in records if r.scope.startswith("elicitation:exclusion:")]
    sentinels = [r for r in records if r.scope.startswith("elicitation:sentinel:")]
    assert [r.target_column for r in exclusions] == ["review_status"]
    assert not sentinels, [r.scope for r in sentinels]


def test_s6_ignores_a_sentinel_in_a_column_nothing_would_group_by() -> None:
    """"There is a null-like value in here somewhere" is not a gap on a free-text field. The
    evidence that a column *is* grouped by is that its whole vocabulary came back under the read
    cap -- at the cap the count stops being a count and becomes a lower bound."""
    from governed_bi.curator.elicitation import generate_candidate_questions
    from governed_bi.serve.fetch import SAMPLE_ROWS_MAX_VALUES

    tables, assets_by_id = _region_schema()
    wide = tuple(f"note {i}" for i in range(SAMPLE_ROWS_MAX_VALUES - 1)) + ("unknown",)
    assert len(wide) == SAMPLE_ROWS_MAX_VALUES
    records = generate_candidate_questions(
        tables, assets_by_id, observed_values={"geo.geografisch.region": wide}
    )
    assert not [r for r in records if r.scope.startswith("elicitation:sentinel:")]


def test_s6_reaches_a_numeric_measure_because_it_is_averaged() -> None:
    """``-1`` in a numeric column is the classic sentinel, and it sorts first, so the capped
    read sees it whenever it is there -- which is why a numeric column needs no cardinality
    test to qualify as one an answer averages."""
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _region_schema(physical_type="numeric")
    values = tuple(str(v) for v in [-1, *range(200)])
    records = generate_candidate_questions(
        tables, assets_by_id, observed_values={"geo.geografisch.region": values}
    )
    (s6,) = [r for r in records if r.scope.startswith("elicitation:sentinel:")]
    assert "'-1'" in s6.question, s6.question


def test_s6_is_silent_on_a_schema_whose_sentinels_are_not_english() -> None:
    """The limit that survives, stated rather than implied. Removing the *name* gate is what the
    doc asked for and is done; ``_SENTINEL_VALUES`` is still an English word list, which is the
    same failure ``curator/gaps.py`` exists because of. On German ``beer_factory`` this finds
    nothing, and ``orders.status = 'cancelled'`` is missed for the vocabulary, not for E's name
    gate."""
    from governed_bi.curator.elicitation import generate_candidate_questions

    tables, assets_by_id = _region_schema()
    records = generate_candidate_questions(
        tables,
        assets_by_id,
        observed_values={"geo.geografisch.region": ("bayern", "hessen", "unbekannt")},
    )
    assert not [r for r in records if r.scope.startswith("elicitation:sentinel:")]


def _region_schema(*, physical_type: str = "text") -> tuple[list[Any], dict[str, Any]]:
    """One table, one column, named nothing any keyword list contains."""
    from governed_bi.corpus.schema import ColumnAsset, TableAsset

    column = ColumnAsset(
        id="geo.geografisch.region", schema="geo", parent_table="geo.geografisch",
        physical_name="region", summary="region", physical_type=physical_type,
    )
    table = TableAsset(
        id="geo.geografisch", schema="geo", physical_name="geografisch", summary="geografisch",
        columns=(column.id,),
    )
    return [table], {table.id: table, column.id: column}
