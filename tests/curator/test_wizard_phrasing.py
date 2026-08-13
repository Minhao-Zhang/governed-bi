"""What a Setup Wizard question actually says, and to whom.

Detection is measured elsewhere (``test_gaps.py``, ``test_gap_signals.py``). This file is about
the other half: a gap that is correctly found, correctly tiered and **phrased so that the
audience it claims can answer it**. Two properties, and they pull in opposite directions, which
is the whole point:

* a **business**-audience question must contain no raw schema identifier
  (``serve/schema_term_guard.find_schema_leak``) — Kindling's restaurant owner has never seen a
  column name, and ``playstore.Type`` is unanswerable to them;
* a **data**-audience question must name a real object of the schema — Power Kiosk's DBA needs
  the exact identifier, and a "plain language" rewrite would make those questions unanswerable
  in the opposite direction.

Assertions here are **properties over every generated question**, not string equality against a
template, because a template can be edited and a property cannot be edited away by accident.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

from gaps_fixtures import MeasuredConnector, beer_factory_assets  # noqa: E402

pytestmark = needs("D")


#: A small English schema whose columns hit the keyword generator's gates, so B, C, E and S6
#: appear alongside the structural detectors' output. ``NaN`` is in ``distribution_type`` on
#: purpose: it is a real value of ``app_store.playstore.Type`` and it is camelCase-shaped, which
#: is the one case where a business question's payload must carry an identifier-shaped token.
_ENGLISH_VALUES: dict[str, tuple[str, ...]] = {
    "distribution_type": ("Free", "Paid", "NaN"),
    "review_status": ("approved", "pending", "unknown"),
    "region": ("bay area", "napa valley", "unknown"),
}


def _english_schema() -> tuple[list[Any], dict[str, Any]]:
    from governed_bi.corpus.schema import ColumnAsset, LogicalType, TableAsset

    def column(name: str, *, logical: Any = None, physical: str = "text") -> Any:
        return ColumnAsset(
            id=f"shop.app_listings.{name}",
            schema="shop",
            parent_table="shop.app_listings",
            physical_name=name,
            summary=name,
            physical_type=physical,
            logical_type=logical,
        )

    columns = [
        column("listing_id", physical="bigint"),
        column("listed_on", logical=LogicalType.date, physical="date"),
        column("total_amount", physical="numeric"),
        column("distribution_type"),
        column("review_status"),
        column("region"),
    ]
    table = TableAsset(
        id="shop.app_listings",
        schema="shop",
        physical_name="app_listings",
        summary="app_listings",
        columns=tuple(c.id for c in columns),
    )
    return [table], {a.id: a for a in [table, *columns]}


class _ScriptedConnector:
    """``tests/serve/test_agent_tools_hitl.py``'s ``Recorder`` idiom: keyed on the quoted column
    identifier the governed statement carries."""

    dialect = "postgres"

    def execute(self, sql: str, **_kwargs: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        for name, values in _ENGLISH_VALUES.items():
            if f'"{name}"' in sql:
                return ([name], [(v,) for v in values], False)
        return ([], [], False)


def _keyword_records() -> list[Any]:
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.curator.elicitation import generate_candidate_questions, read_observed_values
    from governed_bi.govern.policy import GovernancePolicy

    tables, assets_by_id = _english_schema()
    observed, _ledger = read_observed_values(
        tables,
        assets_by_id,
        connector=_ScriptedConnector(),
        corpus=for_analyst(list(assets_by_id.values())),
        policy=GovernancePolicy(),
    )
    return generate_candidate_questions(tables, assets_by_id, observed_values=observed)


def _structural_records() -> list[Any]:
    """The real German ``beer_factory`` schema, which is where the business-audience leak was
    worst: ``wurzelbier_bewertung`` is snake_case, so ``find_schema_leak`` sees it as an
    identifier and a German-speaking restaurant owner sees a table name."""
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.curator.gaps import detect_structural_gaps
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.retrieve.structure import build_structure

    assets = beer_factory_assets()
    structure, _problems = build_structure(list(assets.values()))
    tables = [a for a in assets.values() if a.asset_type.value == "table"]
    scan = detect_structural_gaps(
        tables,
        assets,
        connector=MeasuredConnector(),
        corpus=for_analyst(list(assets.values())),
        policy=GovernancePolicy(),
        join_edges=structure.join_edges,
    )
    return list(scan.records)


def _presented() -> list[Any]:
    """Every candidate an admin is shown, as ``POST /elicitation/generate`` assembles them."""
    from governed_bi.curator.candidate_rules import enforce_audience_language

    return enforce_audience_language([*_structural_records(), *_keyword_records()])


def _physical_names() -> set[str]:
    assets = {**beer_factory_assets(), **_english_schema()[1]}
    return {
        str(a.physical_name)
        for a in assets.values()
        if getattr(a, "physical_name", None)
    }


# ── the asymmetry ───────────────────────────────────────────────────────────────────────────


def test_no_business_audience_question_contains_a_raw_schema_identifier() -> None:
    from governed_bi.serve.schema_term_guard import find_schema_leak

    business = [r for r in _presented() if r.audience == "business"]
    assert business, "expected the business tab to have questions at all"
    for rec in business:
        leak = find_schema_leak(rec.question)
        assert leak is None, f"{rec.scope}: {leak!r} in {rec.question!r}"


def test_every_business_audience_question_still_says_which_table_it_is_about() -> None:
    """The other half of the leak fix, and it is a real constraint rather than a nicety.

    The wizard card used to render ``{target_table}.{target_column}`` in monospace beneath every
    question, which put ``mobile_app_market.content_rating`` on the business tab whatever the
    question said — so the card is now identifier-free for that audience, and the question text
    is the only thing left that can disambiguate. ``app_store`` is the case that proves it
    matters: ``playstore`` and ``mobile_app_market`` **both** hold a ``content_rating`` column,
    so a question naming only the field would appear twice, identically, with no way to tell
    which was which.
    """
    from governed_bi.curator.elicitation import plain_name

    for rec in _presented():
        if rec.audience != "business" or not rec.target_table:
            continue
        assert plain_name(rec.target_table) in rec.question, f"{rec.scope}: {rec.question!r}"


def test_every_data_audience_question_names_a_real_object_of_the_schema() -> None:
    """The other direction, and it is not the same test negated. A DBA has to be able to go and
    look at the thing, so the question (or the choices it offers) must carry a real physical
    name — a "plain language" rewrite of these would be the mirror-image defect."""
    names = _physical_names()
    for rec in [r for r in _presented() if r.audience == "data"]:
        text = " ".join([rec.question, *(str(c["label"]) for c in rec.choices or ())])
        assert any(name in text for name in names), f"{rec.scope}: names nothing — {text!r}"


def test_the_guard_moves_a_leaking_business_question_to_the_data_tab() -> None:
    """A control, not a convention. Every template below currently passes on its own, which is
    the state this is meant to keep: the guard exists so that the *next* edit to a business
    template cannot ship a question its audience cannot read."""
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.candidate_rules import enforce_audience_language

    leaking = ClarificationRecord(
        id="q1",
        scope="elicitation:valuemap:orders.unit_price",
        question="Which values of `orders.unit_price` count together?",
        category="B",
        severity="T2",
        audience="business",
    )
    (out,) = enforce_audience_language([leaking])
    assert out.audience == "data"
    assert out.question == leaking.question, "the finding is moved, never reworded or dropped"


def test_the_guard_leaves_a_data_audience_question_alone() -> None:
    """The asymmetry, pinned: the same text that disqualifies a business question is what makes
    a data question answerable."""
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.candidate_rules import enforce_audience_language

    engineering = ClarificationRecord(
        id="q2",
        scope="elicitation:duplicate:orders.unit_price|unit_price_v2",
        question="`orders.unit_price` and `orders.unit_price_v2` disagree. Which is authoritative?",
        category="D",
        severity="T1",
        audience="data",
    )
    (out,) = enforce_audience_language([engineering])
    assert out is engineering


def test_a_verbatim_database_value_never_trips_the_guard() -> None:
    """The known limitation of shape detection, arriving as a real value rather than as a
    hypothetical: ``app_store.playstore.Type`` holds ``NaN``, which is camelCase-shaped. B's
    whole contract is that a domain owner picks from the stored values rather than typing one,
    so the value has to be shown byte-exact — and a proper noun can only honestly reach a
    business question as a value, never as prose we wrote."""
    from governed_bi.curator.candidate_rules import enforce_audience_language
    from governed_bi.serve.schema_term_guard import find_schema_leak

    assert find_schema_leak("NaN") == "NaN", "the guard really would flag this token"
    value_maps = [
        r
        for r in _keyword_records()
        if r.scope == "elicitation:valuemap:app_listings.distribution_type"
    ]
    assert value_maps, "expected a B question over the column holding NaN"
    (rec,) = enforce_audience_language(value_maps)
    assert rec.audience == "business"
    assert "NaN" in {c["id"] for c in rec.choices or ()}


# ── templates carried over from v1 ──────────────────────────────────────────────────────────


def test_no_question_carries_a_worked_example_from_the_column_it_was_written_for() -> None:
    """v1's B template ended ``(e.g. 'domestic')`` and its E template asked whether a value
    ``means 'not yet rated'``. Both were written against one column — a country column and a
    rating column — and both shipped verbatim onto every column their gate admits, so a
    Free/Paid column was asked about "domestic" and a ``status`` column about "not yet rated".
    """
    stale = ("domestic", "not yet rated", "not_yet_rated")
    for rec in _presented():
        text = " ".join([rec.question, *(str(c["label"]) for c in rec.choices or ())])
        for phrase in stale:
            assert phrase not in text, f"{rec.scope}: {phrase!r} in {text!r}"


def test_an_agreeing_pair_is_not_described_as_holding_different_values() -> None:
    """The same defect class inside one template rather than across columns: the T4 branch
    reused the disagreement sentence and read "hold different values on 0 of N rows — they agree
    everywhere", which contradicts itself in eleven words."""
    agreeing = [
        r
        for r in _structural_records()
        if r.scope.startswith("elicitation:duplicate:") and r.severity == "T4"
    ]
    assert agreeing, "beer_factory has two decoy pairs that genuinely agree row-wise"
    for rec in agreeing:
        assert "different values" not in rec.question, rec.question
