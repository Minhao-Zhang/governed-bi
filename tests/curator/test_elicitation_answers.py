"""What an answered Setup Wizard question becomes in the corpus.

``d17d6e0`` fixed one instance of a defect class: category D's branch handled only freeform,
so clicking a choice on the wizard's highest-severity question composed ``""`` and
``fold_ledger_answer_into_corpus``'s "no answer text" gate then folded nothing — the admin's
decision recorded as answered and lost. It was one instance because the branch that lost it was
the one nobody had exercised with the other input shape.

So the contract this file pins is the class, not the instance: **for every question the wizard
can ask, both input shapes compose a well-formed, self-contained sentence.** The checklist form
(``components/corpus/elicitation-checklist-form.tsx``) submits picks and freeform together, and
``ClarificationAnswerForm`` submits either, so all three arrivals are real.

"Self-contained" is load-bearing rather than stylistic: the composed text is what
``curator/clarification.py::draft_from_clarification`` writes into a ``TermAsset``'s indexed
``summary``, with nothing but the question beside it. A bare picked label (``"orders.total"``)
or a bare "yes" says nothing there.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

from gaps_fixtures import (  # noqa: E402
    BEER_FACTORY_OBSERVED,
    MeasuredConnector,
    beer_factory_assets,
)

pytestmark = needs("D")

_TERMINAL = (".", "!", "?", "…")


def _english_records() -> list[Any]:
    """A/B/C/E/S6, from the keyword generator over a schema whose names hit its gates."""
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.corpus.schema import ColumnAsset, LogicalType, TableAsset
    from governed_bi.curator.elicitation import generate_candidate_questions, read_observed_values
    from governed_bi.govern.policy import GovernancePolicy

    values = {
        "country_code": ("CA", "MX", "US"),
        "review_status": ("approved", "pending", "unknown"),
    }

    class Connector:
        dialect = "postgres"

        def execute(self, sql: str, **_k: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
            for name, vals in values.items():
                if f'"{name}"' in sql:
                    return ([name], [(v,) for v in vals], False)
            return ([], [], False)

    columns = [
        ColumnAsset(
            id=f"shop.orders.{name}",
            schema="shop",
            parent_table="shop.orders",
            physical_name=name,
            summary=name,
            physical_type=physical,
            logical_type=logical,
        )
        for name, physical, logical in (
            ("order_id", "bigint", None),
            ("order_date", "date", LogicalType.date),
            ("total_amount", "numeric", None),
            ("country_code", "text", None),
            ("review_status", "text", None),
        )
    ]
    table = TableAsset(
        id="shop.orders",
        schema="shop",
        physical_name="orders",
        summary="orders",
        columns=tuple(c.id for c in columns),
    )
    assets = {a.id: a for a in [table, *columns]}
    observed, _ledger = read_observed_values(
        [table],
        assets,
        connector=Connector(),
        corpus=for_analyst(list(assets.values())),
        policy=GovernancePolicy(),
    )
    return generate_candidate_questions([table], assets, observed_values=observed)


def _structural_records() -> list[Any]:
    """S1/S2/S3, from the real German ``beer_factory`` schema and its real row counts.

    ``with_joins=False`` and the real observed values, because both join question shapes
    (a single candidate key, and two that disagree) only exist for a table pair with no declared
    join — which is 28 of ``beer_factory``'s 36 pairs in reality anyway.
    """
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.curator.gaps import detect_structural_gaps
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.retrieve.structure import build_structure

    assets = beer_factory_assets(with_joins=False)
    structure, _problems = build_structure(list(assets.values()))
    tables = [a for a in assets.values() if a.asset_type.value == "table"]
    scan = detect_structural_gaps(
        tables,
        assets,
        connector=MeasuredConnector(),
        corpus=for_analyst(list(assets.values())),
        policy=GovernancePolicy(),
        join_edges=structure.join_edges,
        observed_values=BEER_FACTORY_OBSERVED,
    )
    return list(scan.records)


def _every_record() -> list[Any]:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation import maybe_generate_join_followup

    records = [*_structural_records(), *_english_records()]
    followup = maybe_generate_join_followup(
        ClarificationRecord(
            id="q",
            scope="elicitation:term:amount",
            question="?",
            category="A",
            target_table="orders",
        ),
        "payments.revenue_amount",
    )
    assert followup is not None, "the D auto-follow-up is a question the wizard can ask too"
    return [*records, followup]


def _kinds(records: list[Any]) -> set[str]:
    return {r.scope.split(":")[1] for r in records}


def _well_formed(text: str, record: Any) -> None:
    """Non-empty, one terminator, and it says what it is about.

    The last clause is why the composed sentence exists at all: it lands in a ``TermAsset``'s
    indexed ``summary``, where "Free, Paid" or "yes" attaches to nothing. A record with no
    ``target_table`` is schema-wide by construction (C's fiscal-year constant is the only one)
    and has no object to name.
    """
    assert text, f"{record.scope}: composed nothing"
    assert text.strip() == text, f"{record.scope}: untrimmed — {text!r}"
    assert text.endswith(_TERMINAL), f"{record.scope}: not a sentence — {text!r}"
    assert ".." not in text, f"{record.scope}: doubled terminator — {text!r}"
    subject = record.target_table or ""
    if record.scope.startswith("elicitation:term:"):
        # A's ``target_table`` is the *expected* table for the join heuristic, not the subject:
        # the admin may pick a column on any table, and what the fact is about is the term.
        subject = record.scope.rsplit(":", 1)[-1]
    if subject:
        assert subject in text, f"{record.scope}: names no object — {text!r}"


# ── the contract, over every question the wizard can ask ────────────────────────────────────


def test_the_generators_between_them_cover_every_question_shape() -> None:
    """Guards the two tests below from passing vacuously: they are only a class-wide contract
    if the class is actually present in what they iterate over."""
    assert _kinds(_every_record()) == {
        "describecolumns",
        "describetable",
        "duplicate",
        "join",
        "joinkey",
        "joinkeys",
        "rule",
        "term",
        "valuemap",
        "exclusion",
    }


def test_a_picked_choice_composes_a_well_formed_sentence_for_every_question() -> None:
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    for record in _every_record():
        if not record.choices:
            continue
        first = str(record.choices[0]["id"])
        if record.ui_modality == "checklist":
            text = compose_elicitation_answer_text(record, choice_ids=[first])
        else:
            text = compose_elicitation_answer_text(record, choice_id=first)
        _well_formed(text, record)


def test_a_freeform_answer_composes_a_well_formed_sentence_for_every_question() -> None:
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    for record in _every_record():
        text = compose_elicitation_answer_text(record, freeform="whatever the admin typed")
        _well_formed(text, record)
        assert "whatever the admin typed" in text, record.scope


def test_picks_and_freeform_together_keep_both() -> None:
    """The checklist form submits both in one payload, so "whichever one was supplied" is not
    the whole contract — losing either half is the same silent-drop defect."""
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    for record in _every_record():
        if record.ui_modality != "checklist":
            continue
        picks = [str(c["id"]) for c in record.choices or ()][:2]
        text = compose_elicitation_answer_text(
            record, choice_ids=picks, freeform="what they mean"
        )
        _well_formed(text, record)
        assert "what they mean" in text, record.scope
        for pick in picks:
            assert pick in text, f"{record.scope}: dropped {pick!r} — {text!r}"


def test_an_answer_that_already_ends_in_a_full_stop_does_not_get_a_second_one() -> None:
    """The measured defect: a freeform table description composed
    ``"'app_store.playstore' maps to One row per app listing.."``"""
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    for record in _every_record():
        text = compose_elicitation_answer_text(record, freeform="One row per app listing.")
        assert ".." not in text, f"{record.scope}: {text!r}"


# ── S1: the questions that composed nothing at all ──────────────────────────────────────────


def test_describing_a_table_is_a_description_not_a_term_mapping() -> None:
    """S1's two questions are category A because A is the closest of the five letters, and they
    were composing through A's "term maps to column" frame — which reads the *scope tail* as the
    term. Measured: a freeform answer composed
    ``"'app_store.playstore' maps to One row per app listing.."``, which is not what was asked,
    not what was answered, and has two full stops.
    """
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    (record,) = [
        r
        for r in _structural_records()
        if r.scope == "elicitation:describetable:beer_factory.kunden"
    ]
    text = compose_elicitation_answer_text(record, freeform="One customer of the brewery.")
    assert text == "What one row of kunden represents: One customer of the brewery."


def test_describing_several_columns_at_once_composes_all_three_ways() -> None:
    """The measured failure: the checklist submits ``choice_ids``, A's branch reads only
    ``choice_id``, so a checked answer composed ``""`` and folded nothing — 18 of
    ``beer_factory``'s 55 cards are S1.
    """
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    (record,) = [
        r
        for r in _structural_records()
        if r.scope == "elicitation:describecolumns:beer_factory.kunden"
    ]
    both = compose_elicitation_answer_text(
        record, choice_ids=["stadt", "ort"], freeform="both hold the billing city"
    )
    assert both == "In kunden, stadt and ort: both hold the billing city."

    picks_only = compose_elicitation_answer_text(record, choice_ids=["stadt"])
    assert picks_only == (
        "In kunden, stadt needs a description: its name does not say what it holds."
    )

    freeform_only = compose_elicitation_answer_text(record, freeform="all self-explanatory")
    assert freeform_only == "About the columns of kunden: all self-explanatory."


def test_a_picked_choice_and_freeform_are_both_kept_on_an_exclusion() -> None:
    """"Leave them out, except for the 2019 backfill" is two facts. Every other shape treats
    freeform as overriding the pick, because there the two answer the same question; here the
    freeform is a qualifier on the pick and dropping either loses half the decision."""
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    record = _exclusion_record()
    both = compose_elicitation_answer_text(
        record, choice_id="exclude", freeform="except the 2019 backfill"
    )
    assert both == (
        "orders.review_status — Leave out the rows where review status is 'pending'; "
        "except the 2019 backfill."
    )


def test_a_suspect_column_answer_describes_the_column_it_names() -> None:
    """The same A-frame defect on the fourth question that borrows category A. It cannot fire on
    a seeded corpus (``gaps.py::_reliability_records``), so it is built here rather than scanned
    — but it composes through the same path and was wrong in the same way."""
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    record = ClarificationRecord(
        id="q",
        scope="elicitation:reliability:beer_factory.kunden.email",
        question="?",
        category="A",
        target_table="kunden",
        target_column="email",
    )
    text = compose_elicitation_answer_text(record, freeform="backfilled before 2019, unreliable")
    assert text == "kunden.email: backfilled before 2019, unreliable."


# ── one composed sentence per question shape (moved with the module from
# ``test_elicitation.py``, and restated where the composition changed) ──────────────────────


def _exclusion_record() -> Any:
    from governed_bi.curator.clarifications import ClarificationRecord

    return ClarificationRecord(
        id="q",
        scope="elicitation:exclusion:orders.review_status",
        question="?",
        category="E",
        ui_modality="checkbox",
        choices=(
            {"id": "exclude", "label": "Leave out the rows where review status is 'pending'"},
            {"id": "include", "label": "Count them; 'pending' is a real value here"},
        ),
        target_table="orders",
        target_column="review_status",
        source="elicitation_wizard",
    )


def _valuemap_record() -> Any:
    from governed_bi.curator.clarifications import ClarificationRecord

    return ClarificationRecord(
        id="q",
        scope="elicitation:valuemap:orders.country_code",
        question="?",
        category="B",
        ui_modality="checklist",
        choices=tuple({"id": v, "label": v} for v in ("US", "CA", "MX")),
        target_table="orders",
        target_column="country_code",
        source="elicitation_wizard",
    )


def test_a_term_mapping_names_the_term_and_the_column() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    rec = ClarificationRecord(
        id="q",
        scope="elicitation:term:revenue",
        question="?",
        category="A",
        choices=({"id": "payments.revenue_amount", "label": "payments.revenue_amount"},),
        source="elicitation_wizard",
    )
    assert compose_elicitation_answer_text(rec, choice_id="payments.revenue_amount") == (
        "'revenue' maps to payments.revenue_amount."
    )
    assert compose_elicitation_answer_text(rec, freeform="orders.grand_total") == (
        "'revenue' maps to orders.grand_total."
    )


def test_a_business_rule_constant_reads_the_same_from_either_input() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    rec = ClarificationRecord(
        id="q",
        scope="elicitation:rule:fiscal_year_start",
        question="?",
        category="C",
        ui_modality="numeric",
        choices=({"id": "10", "label": "10 - October"},),
        source="elicitation_wizard",
    )
    assert compose_elicitation_answer_text(rec, freeform="4") == "Fiscal year starts in month 4."
    assert compose_elicitation_answer_text(rec, choice_id="10") == (
        "Fiscal year starts in month 10 - October."
    )
    assert compose_elicitation_answer_text(rec, freeform="") == ""


def test_an_exclusion_names_the_physical_column_even_though_the_question_did_not() -> None:
    """The asymmetry that makes both halves work: the *question* says ``'review status'`` because
    a business owner is reading it, and the *fact* says ``orders.review_status`` because the
    retrieval layer is. The label used to be the only thing carrying the column, so a
    business-language label would have folded an exclusion of nothing in particular."""
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    rec = _exclusion_record()
    assert compose_elicitation_answer_text(rec, choice_id="exclude") == (
        "orders.review_status — Leave out the rows where review status is 'pending'."
    )
    assert compose_elicitation_answer_text(rec, choice_id="include") == (
        "orders.review_status — Count them; 'pending' is a real value here."
    )
    assert compose_elicitation_answer_text(rec, freeform="only when the reviewer was a bot") == (
        "orders.review_status — only when the reviewer was a bot."
    )


def test_a_value_grouping_keeps_the_values_and_the_name_the_admin_gave_them() -> None:
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    rec = _valuemap_record()
    assert compose_elicitation_answer_text(rec, choice_ids=["US", "CA"]) == (
        "In orders.country_code, these values count as one group: US and CA."
    )
    assert compose_elicitation_answer_text(
        rec, choice_ids=["US", "CA"], freeform="North America"
    ) == "In orders.country_code, 'North America' means US and CA."
    assert compose_elicitation_answer_text(rec, freeform="Anything in North America") == (
        "For orders.country_code: Anything in North America."
    )
    assert compose_elicitation_answer_text(rec, choice_ids=[]) == ""


def test_a_duplicate_column_pick_keeps_the_label_and_names_both_columns_for_the_neither_option() -> None:
    """``d17d6e0``'s case, still pinned, plus the one it left: "They are different fields, both
    correct" names neither field, so on its own it folds as a fact about nothing. The two
    columns are recovered from the record's own other choices."""
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    rec = ClarificationRecord(
        id="q",
        scope="elicitation:duplicate:playstore.Content Rating|content_rating",
        question="?",
        category="D",
        ui_modality="column_picker",
        target_table="playstore",
        choices=(
            {
                "id": "playstore.Content Rating",
                "label": "playstore.Content Rating is authoritative",
            },
            {
                "id": "playstore.content_rating",
                "label": "playstore.content_rating is authoritative",
            },
            {"id": "different_fields", "label": "They are different fields, both correct"},
        ),
    )
    assert compose_elicitation_answer_text(rec, choice_id="playstore.Content Rating") == (
        "playstore.Content Rating is authoritative."
    )
    assert compose_elicitation_answer_text(rec, choice_id="different_fields") == (
        "playstore.Content Rating and playstore.content_rating: they are different fields, "
        "both correct."
    )
    # Freeform still wins when both arrive, matching every other picker.
    assert compose_elicitation_answer_text(
        rec, choice_id="playstore.Content Rating", freeform="neither; both are imports"
    ) == "playstore: neither; both are imports."


def test_a_join_answer_says_which_object_it_is_about() -> None:
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    followup = ClarificationRecord(
        id="q",
        scope="elicitation:join:orders:payments",
        question="?",
        category="D",
        target_table="payments",
        target_column="order_id",
    )
    assert compose_elicitation_answer_text(
        followup, freeform="orders.id = payments.order_id"
    ) == "payments.order_id: orders.id = payments.order_id."

    ambiguous = ClarificationRecord(
        id="q",
        scope="elicitation:joinkeys:beer_factory.kunden|beer_factory.transaktion",
        question="?",
        category="D",
        ui_modality="column_picker",
        target_table="transaktion",
        choices=(
            {"id": "transaktion.kunde_id", "label": "transaktion.kunde_id"},
            {"id": "transaktion.transaktions_kunde_id", "label": "transaktion.transaktions_kunde_id"},
        ),
    )
    assert compose_elicitation_answer_text(ambiguous, choice_id="transaktion.kunde_id") == (
        "kunden and transaktion join on transaktion.kunde_id."
    )


def test_a_scope_no_composer_knows_still_keeps_the_answer() -> None:
    """The fallback is freeform-or-label, never the picked *id* and never ``""`` — a category
    the table has not learned yet must degrade to "less context", not to "answer discarded"."""
    from governed_bi.curator.clarifications import ClarificationRecord
    from governed_bi.curator.elicitation_answers import compose_elicitation_answer_text

    rec = ClarificationRecord(
        id="q",
        scope="elicitation:somethingnew:orders",
        question="?",
        category="A",
        choices=({"id": "opt", "label": "the first option"},),
    )
    assert compose_elicitation_answer_text(rec, choice_id="opt") == "the first option."
    assert compose_elicitation_answer_text(rec, freeform="typed instead") == "typed instead."
