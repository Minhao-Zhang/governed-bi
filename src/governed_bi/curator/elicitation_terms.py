"""Category A, the wizard's one hybrid gap: what a business term means, and which column holds it.

Split out of ``curator/elicitation.py`` (ADR 0005 §6) along the seam the gap model draws rather
than a line count: A is the only keyword category that resolves into **two** records with a
dependency between them, and the only one that pays for a governed *count* rather than a value
read. Everything else that module proposes is one record, one audience, one payload.

``detent-ai-setup-wizard-gap-model.md`` § "Which gap types produce two audience-specific
questions", point 1: the shipped single question

    "When you say 'revenue', which table/column does that map to?"
    [ payments.amount ] [ line_items.unit_price ]

is unanswerable from both ends at once. Kindling's restaurant owner has never seen those names;
Power Kiosk's DBA can only guess at what the business calls revenue. So it becomes an ordered
pair — **A-biz** on the business tab (which meaning), **A-eng** on the data tab (which column),
the second ``blocked_by`` the first.

**What grounds an A-biz choice, and what does not.** This is the whole design decision, and the
doc's own worked example over-promises it. Its three options — "what customers were actually
charged after discounts", "the list price of what they ordered", "what you were actually paid
after refunds and card fees" — are business facts about a company's commercial arrangements.
Nothing about a column name, its type, its values or its counts can distinguish a price net of
refunds from one gross of them. Generating those three from a schema would be inventing them,
and a question whose options are plausible and unfounded teaches an admin to rubber-stamp: the
same defect as v1's ``(e.g. 'domestic')`` shipping onto a Free/Paid column, one register worse
because it would read as insight rather than as a stale example.

So an A-biz choice states only what was measured or read, in the customer's own words:

* **where the value is recorded** — the table and the field, through
  :func:`~governed_bi.curator.elicitation.plain_name`, which drops the separators and case seams
  that make a token read as an identifier and changes nothing else. That is the customer's
  vocabulary, not a guess at what they meant by it.
* **how it varies** — :func:`read_term_cardinalities`' governed ``count(*)``/``count(distinct)``.
  "a separate value on every one of its 6 312 records" versus "42 different values across 6 312
  records" is the *grain* distinction, and grain is what actually separates a list price on a
  product from a charged price on an order line. It is the doc's example's real content, arrived
  at by counting rows instead of by asserting a commercial policy.

**A consequence worth stating rather than hiding: a grounded A-biz choice is a proxy for its
column.** One choice is minted per candidate column, so picking a meaning implies a column. The
doc's example escapes that only because its meanings are invented and merely happen to
correspond. What the split still buys is real and is the point of the audience axis: the person
who knows the business chooses, in language they can read, without ever being shown a name they
cannot; and A-eng then binds that choice with evidence they could not have seen — and may
override it, because the field whose plain name sounds right is not always the column that holds
the fact.

**Two candidates minimum for A-biz.** The doc's ``A″`` row: a single-choice picker is a forced
answer, not a disambiguation (measured on ``bird_rootbeer_en``, where "cost" offered
``rootbeerbrand.WholesaleCost`` alone). With one candidate there is no meaning to choose between,
so no business question is minted. A-eng still is — with one candidate it is a confirmation a DBA
can give, and suppressing it would drop a finding, which is a different decision than this
module's.

**What is not claimed, because it could not be read.** The doc asks A-eng to show a null count;
no statement on the governed path reports one (``serve/fetch.column_cardinality_statement`` is
``count(*)`` and ``count(distinct c)``, and ``distinct_values_statement`` filters ``IS NOT
NULL``), so none is shown. Sample values are labelled "e.g." and never as a range: the read is
``ORDER BY c LIMIT 20``, so what comes back is the *lowest* values, and a column's maximum is
not among the things this has seen.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from governed_bi.curator.clarifications import ClarificationRecord
from governed_bi.curator.elicitation import (
    CATEGORY_CLASSIFICATION,
    ELICITATION_SOURCE,
    _columns_of,
    _live_tables,
    _record_id,
    plain_name,
)

__all__ = [
    "AMBIGUOUS_TERMS",
    "MAX_CARDINALITY_READS",
    "A_BIZ_SCOPE_PREFIX",
    "A_ENG_SCOPE_PREFIX",
    "term_candidates",
    "read_term_cardinalities",
    "propose_term_questions",
    "business_definition",
    "restate_with_business_definition",
]

#: The ambiguous business terms A looks for in a column name — v1's list, unchanged, moved here
#: with the only category that reads it. Extending coverage means growing this list; it is also
#: the reason A finds nothing on German ``beer_factory``, which is ``curator/gaps.py``'s subject
#: and not this module's.
AMBIGUOUS_TERMS: tuple[str, ...] = (
    "revenue", "cost", "profit", "total", "amount", "price", "balance", "value",
)

#: Scope prefixes for the two halves. A-biz keeps ``elicitation:term:``, which is not cosmetic:
#: ``curator/candidate_rules.drop_already_answered`` suppresses a candidate under that prefix
#: when a **certified** ``TermAsset`` already defines the term, and a certified term is a
#: business definition arrived at without the wizard — it settles the business half and nothing
#: else. A-eng gets its own prefix so that same rule does not reach it: a definition of "price"
#: is not a statement about which column holds it.
A_BIZ_SCOPE_PREFIX = "elicitation:term:"
A_ENG_SCOPE_PREFIX = "elicitation:termcolumn:"

#: Ceiling on governed cardinality statements per :func:`read_term_cardinalities` call.
#:
#: A **cost** bound of the same kind as ``elicitation.MAX_VALUE_READS`` and
#: ``gaps.MAX_PAIR_COMPARISONS``, and a much smaller one because the population is: only columns
#: whose *name* contains one of :data:`AMBIGUOUS_TERMS` are read, which is 2 on ``app_store``, 2
#: on ``gbi_demo_sales`` and 0 on German ``beer_factory``. 200 is far above every schema in the
#: lake and exists so that one admin click on some future warehouse whose every column is called
#: ``*_amount`` cannot become thousands of statements. It truncates in a stated order (candidates
#: are collected table-id-sorted, columns in their table's own order), so which columns a capped
#: call reads does not move between runs.
MAX_CARDINALITY_READS = 200


def term_candidates(
    tables: Sequence[Any], assets_by_id: dict[str, Any]
) -> list[tuple[str, list[tuple[Any, Any]]]]:
    """``[(term, [(table, column), …])]`` for every ambiguous term some column name contains.

    One implementation, three readers — the two question templates and the cardinality read —
    so "which columns is the wizard asking about for this term" cannot come to have two answers.
    Ordered by :data:`AMBIGUOUS_TERMS` and then by qualified name, so the proposed set and the
    read order are the same on every run.
    """
    live = _live_tables(tables)
    out: list[tuple[str, list[tuple[Any, Any]]]] = []
    for term in AMBIGUOUS_TERMS:
        matches = [
            (table, column)
            for table in live
            for column in _columns_of(table, assets_by_id)
            if term in column.physical_name.lower()
        ]
        if matches:
            matches.sort(key=lambda tc: (tc[0].physical_name, tc[1].physical_name))
            out.append((term, matches))
    return out


def read_term_cardinalities(
    tables: Sequence[Any],
    assets_by_id: dict[str, Any],
    *,
    connector: Any,
    corpus: Any,
    policy: Any,
    max_reads: int = MAX_CARDINALITY_READS,
) -> tuple[dict[str, Any], tuple[Any, ...]]:
    """``({column id: ColumnCardinality}, ledger rows)`` for every A candidate column.

    ``serve/fetch.count_distinct_values`` — the same ``prepare()``-checked, ledgered path
    ``curator/gaps.py``'s join detector already uses to ask whether a column identifies a row.
    Reused rather than reassembled here for ``read_observed_values``' reason: a second copy of
    that body would be a second answer to what a governed count checks, and this caller needs
    none of the checks relaxed.

    **A second statement per column, and not one that could be folded into the value read.**
    ``distinct_values_statement`` returns at most ``SAMPLE_ROWS_MAX_VALUES`` values and therefore
    can never say how many rows a column has, nor whether it has exactly as many distinct values
    as rows — which is the whole of the grain signal. ``column_cardinality_statement`` is the
    question; the values are a different one, already asked elsewhere and for a different reader.

    ``bounds`` licenses exactly the one table the column belongs to, for the same two reasons
    ``read_observed_values`` gives: there is no retrieval to derive a licensed set from, and a
    corpus-wide fold map makes ``name``/``id``/``code`` ambiguous and would refuse almost
    everything. A refusal skips the column and still returns its ledger row.
    """
    if connector is None:
        # No database to read, so nothing measured -- and nothing pretended. Guarded here rather
        # than downstream because ``serve/fetch``'s readers now *raise* on a missing connector
        # (``test_a_wiring_failure_is_not_a_verdict``: a refusal row built in ``serve/`` files our
        # own misconfiguration in the ledger as the layer stack refusing the statement). Before
        # this guard the whole scan propagated that as a 500; a corpus-only session should still
        # get the signals that need no rows, and honestly get none of the ones that do.
        return {}, []

    from governed_bi.govern.bounds import ToolBounds
    from governed_bi.serve.fetch import count_distinct_values

    seen: set[str] = set()
    pairs: list[tuple[Any, Any]] = []
    for _term, matches in term_candidates(tables, assets_by_id):
        for table, column in matches:
            if column.id in seen:
                continue
            seen.add(column.id)
            pairs.append((table, column))

    counts: dict[str, Any] = {}
    ledger: list[Any] = []
    for table, column in pairs[: max(0, int(max_reads))]:
        measured, attempt = count_distinct_values(
            column.id,
            bounds=ToolBounds(licensed=frozenset({table.id})),
            assets=assets_by_id,
            connector=connector,
            corpus=corpus,
            policy=policy,
        )
        if attempt is not None:
            ledger.append(attempt)
        if measured is not None:
            counts[column.id] = measured
    return counts, tuple(ledger)


# ── A-biz: which meaning, in the customer's own words ───────────────────────────────────────


def _where_recorded(table: Any, column: Any) -> str:
    """"the 'unit price' recorded in your order items data" — the one phrase both halves of the
    business choice are built from, and the only thing this module says about what a column
    *means*. It renames nothing and interprets nothing."""
    return (
        f"the {plain_name(column.physical_name)!r} recorded in your "
        f"{plain_name(table.physical_name)} data"
    )


def _how_it_varies(cardinality: Any | None) -> str:
    """The measured grain, in words, or ``""`` when nothing was measured.

    Three states and no fourth: a column whose distinct count reaches its row count carries a
    separate value per record; one below it repeats; and an unread or refused column says
    nothing at all rather than guessing. ``n_distinct`` counts non-null values
    (``count(distinct c)``), so ``>=`` rather than ``==`` — a column with nulls can never reach
    its row count and would otherwise be reported as repeating on the strength of its nulls.
    """
    if cardinality is None or not cardinality.n_rows:
        return ""
    if cardinality.n_distinct >= cardinality.n_rows:
        return f"a separate value on every one of its {cardinality.n_rows} records"
    return f"{cardinality.n_distinct} different values across {cardinality.n_rows} records"


def _business_choices(
    matches: Sequence[tuple[Any, Any]], cardinalities: Mapping[str, Any]
) -> tuple[Mapping[str, str], ...]:
    """One choice per candidate column, described by where it is recorded and how it varies.

    The id stays the qualified physical name because A-eng, ``apply_cluster_dependencies``'s
    contested-column match and the D join follow-up all key on it; the UI renders labels only
    (``components/common/clarification-answer-form.tsx``). The label is authored prose and is
    therefore what ``candidate_rules.enforce_audience_language`` guards — which is the right way
    round, since every part of it comes out of :func:`~governed_bi.curator.elicitation.plain_name`
    and cannot be identifier-shaped by construction.
    """
    choices: list[Mapping[str, str]] = []
    for table, column in matches:
        varies = _how_it_varies(cardinalities.get(column.id))
        where = _where_recorded(table, column)
        choices.append(
            {
                "id": f"{table.physical_name}.{column.physical_name}",
                "label": f"{where} — {varies}" if varies else where,
            }
        )
    return tuple(choices)


# ── A-eng: which column, with what a DBA needs to check it ──────────────────────────────────


def _declared_type(column: Any) -> str:
    """The most specific type the corpus states for this column.

    ``physical_type`` first, because a DBA is going to go and look at the engine's own type and
    ``bigint`` is more use to them than ``numeric``. ``logical_type`` is the fallback for a
    hand-curated corpus that carries one and no physical type; ``type_class`` reads
    ``physical_type`` only, so on such a column it answers ``"unknown"`` while the corpus knows
    better. Neither is inferred from the values.
    """
    from governed_bi.curator.gap_signals import type_class

    if column.physical_type:
        return str(column.physical_type)
    logical = getattr(column, "logical_type", None)
    return str(getattr(logical, "value", logical)) if logical is not None else type_class(column)


def _engineering_choices(
    matches: Sequence[tuple[Any, Any]],
    cardinalities: Mapping[str, Any],
    observed_values: Mapping[str, tuple[str, ...]],
) -> tuple[Mapping[str, str], ...]:
    """One choice per candidate column: the qualified name, then the evidence behind it.

    Everything after the em dash was measured on this database in this scan — the physical type
    off the corpus, the counts from :func:`read_term_cardinalities`, the examples from the value
    read ``curator/elicitation.read_observed_values`` already paid for. Nothing is inferred, and
    the missing null count is missing rather than estimated.

    ``e.g.`` and not a range: ``distinct_values_statement`` orders ascending and takes the first
    twenty, so these are the smallest values the column holds and say nothing about its largest.
    """
    choices: list[Mapping[str, str]] = []
    for table, column in matches:
        qualified = f"{table.physical_name}.{column.physical_name}"
        facts = [_declared_type(column)]
        card = cardinalities.get(column.id)
        if card is not None:
            facts.append(f"{card.n_rows} rows, {card.n_distinct} distinct")
        samples = (observed_values.get(column.id) or ())[:3]
        if samples:
            facts.append("e.g. " + ", ".join(samples))
        choices.append({"id": qualified, "label": f"{qualified} — {'; '.join(facts)}"})
    return tuple(choices)


def propose_term_questions(
    tables: Sequence[Any],
    assets_by_id: dict[str, Any],
    *,
    observed_values: Mapping[str, tuple[str, ...]] | None = None,
    cardinalities: Mapping[str, Any] | None = None,
) -> list[ClarificationRecord]:
    """The A pair for every ambiguous term some column name contains.

    Ordered A-biz before A-eng per term, which is the order the dependency runs in and the order
    a reader of the ledger meets them.

    The prerequisite is **soft on purpose**: ``blocked_by`` withholds A-eng's answer form in the
    wizard, and ``POST /clarifications/{id}/answer`` still accepts an answer to it and stamps
    ``unmet_prerequisites_at_answer``. Power Kiosk has a DBA and no named business-domain expert;
    Kindling has an owner and no DBA. Neither pilot can fill both tabs, so it is the *warrant*
    that differs rather than the availability — see
    ``curator/clarification.py::fold_ledger_answer_into_corpus`` for what the stamp now costs.
    """
    severity, _shipped_audience = CATEGORY_CLASSIFICATION["A"]
    observed = observed_values or {}
    cards = cardinalities or {}

    out: list[ClarificationRecord] = []
    for term, matches in term_candidates(tables, assets_by_id):
        blocked_by: tuple[str, ...] = ()
        if len(matches) > 1:
            biz_scope = f"{A_BIZ_SCOPE_PREFIX}{term}"
            biz_id = _record_id(biz_scope)
            blocked_by = (biz_id,)
            out.append(
                ClarificationRecord(
                    id=biz_id,
                    scope=biz_scope,
                    question=(
                        f"When someone in your business asks about {term!r}, which of these do "
                        "they mean?"
                    ),
                    category="A",
                    # Not ``column_picker``: what is picked is a meaning, and the widget it
                    # renders as (a radio list plus free text) is the same either way. Naming a
                    # modality this question does not have would be the only untrue thing on it.
                    ui_modality=None,
                    severity=severity,
                    audience="business",
                    choices=_business_choices(matches, cards),
                    allow_freeform=True,
                    # No ``target_table``: the question is about a term, and its candidates span
                    # tables. Each choice names its own table in plain words instead, which is
                    # what ``tests/curator/test_wizard_phrasing.py`` requires of a business
                    # question that *has* one table.
                    raised_by=("elicitation_wizard",),
                    source=ELICITATION_SOURCE,
                )
            )
        eng_scope = f"{A_ENG_SCOPE_PREFIX}{term}"
        out.append(
            ClarificationRecord(
                id=_record_id(eng_scope),
                scope=eng_scope,
                question=f"Which column holds {term!r}?",
                category="A",
                ui_modality="column_picker",
                severity=severity,
                audience="data",
                choices=_engineering_choices(matches, cards, observed),
                allow_freeform=True,
                # The "expected" table for the D heuristic: the alphabetically-first candidate,
                # unchanged from the single question this pair replaces.
                target_table=matches[0][0].physical_name,
                blocked_by=blocked_by,
                raised_by=("elicitation_wizard",),
                source=ELICITATION_SOURCE,
            )
        )
    return out


# ── carrying the business answer across to the engineering half ─────────────────────────────


def business_definition(record: Any, freeform: str = "") -> str:
    """What an answered A-biz record says the term means, as the admin left it.

    The picked choice's **label**, the admin's own words, or both — the same two halves and the
    same join ``curator/elicitation_answers.py::_compose_term`` puts in the corpus fact, because
    they are the same answer written for two readers.

    ``freeform`` comes from the caller and not from the record, and it has to:
    ``api/curation_routes.py`` overwrites ``answer`` with the *composed* sentence at answer time,
    so by the time a record is on disk the raw free text is gone. Reading ``record.answer``
    instead is what produced, live on real ``app_store``:

        Business defines 'price' as "In business terms, 'price' means what a shopper pays to
        download the app.". Which column holds that?

    — the corpus frame quoted inside the question frame, two levels of quotation for one
    sentence. The route has the admin's words; only the record does not.
    """
    label = next(
        (
            str(choice.get("label") or "")
            for choice in record.choices or ()
            if str(choice.get("id")) == str(record.answer_choice_id or "")
        ),
        "",
    )
    return "; ".join(p for p in (label, freeform.strip()) if p)


def restate_with_business_definition(
    answered: Any, records: Sequence[Any], *, freeform: str = ""
) -> tuple[str, str] | None:
    """``(A-eng record id, its question with the business definition in it)``, or ``None``.

    Called right after an A-biz answer lands. A-eng exists from scan time rather than being
    minted here — that is what lets a DBA with no business counterpart answer it standalone,
    which is the whole reason the dependency is soft — so what arrives now is the *quote*, and
    the question is restated to carry it:

        Which column holds 'price'?
        → Business defines 'price' as "the 'Price' recorded in your playstore data — 92
          different values across 10 840 records". Which column holds that?

    The whole picked label is quoted, counts included, rather than a prefix of it: those counts
    were measured on this database and are exactly the kind of thing a DBA can check a candidate
    column against.

    ``freeform`` is the admin's own words from the request that answered A-biz, forwarded because
    the record no longer holds them — see :func:`business_definition`.

    ``None`` when the answered record is not an A-biz one, when no A-eng record for the term is
    in the ledger, or when that record is **already answered** — restating an answered question
    would strand the corpus fact folded under its old text, which is the hash
    ``candidate_rules.drop_already_answered`` matches on.
    """
    from governed_bi.curator.clarifications import ClarificationRecordStatus

    if not str(getattr(answered, "scope", "")).startswith(A_BIZ_SCOPE_PREFIX):
        return None
    definition = business_definition(answered, freeform)
    if not definition:
        return None
    term = answered.scope[len(A_BIZ_SCOPE_PREFIX) :]
    for record in records:
        if record.scope != f"{A_ENG_SCOPE_PREFIX}{term}":
            continue
        if record.status is ClarificationRecordStatus.answered:
            return None
        return (
            record.id,
            f"Business defines {term!r} as {definition!r}. Which column holds that?",
        )
    return None
