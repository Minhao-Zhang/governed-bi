"""Phase 2 Setup Wizard: proactive admin onboarding elicitation (DetentAI v1, ported).

Unlike the reactive ``ask_user`` live-chat clarification (``serve/tools.py`` — fires mid-turn
when the live agent is uncertain), this scans an already-known schema **before** any business
user ever asks a question and proposes category-tagged candidate questions for an admin to answer
once — every one its keyword gates find, with no per-category quota (see
:func:`generate_candidate_questions`). This module only decides WHAT to ask; answers reuse the
exact same :class:`~governed_bi.curator.clarifications.ClarificationRecord` ledger + fold
pipeline (``api/curation_routes.py::answer_clarification_route`` ->
``curator/clarification.py::fold_ledger_answer_into_corpus``) as every other clarification
source — no new storage path. **What an answer composes to** is
``curator/elicitation_answers.py``, split out along the seam the sentence above already draws:
this module decides what to ask, that one decides what an answer to it means. The two rules over
an already-assembled candidate set — who may be asked, and what the corpus already answers — are
``curator/candidate_rules.py``, on the same seam from the other side: they generate nothing.

Five categories, fixed priority order (highest first) — :data:`CATEGORY_PRIORITY`:

- **A** — source-of-truth table/column mapping (UI: column picker).
- **C** — business-rule constants (UI: required numeric field).
- **E** — default filter/exclusion logic (UI: exclusion checkbox).
- **B** — value mapping NL<->DB (UI: checklist of real distinct DB values).
- **D** — join paths. Never a standalone question set — only auto-triggered inline
  (:func:`maybe_generate_join_followup`) when an A-answer's picked column lands on a different
  table than schema-inference expected.

**Adapted from v1's shape, not re-derived.** v1's ``TableAsset.columns`` was a list of inline
``Column`` objects; this repo's ``corpus/schema.py::TableAsset.columns`` is a tuple of **column
ids** — each ``ColumnAsset`` is its own entry in ``session.assets_by_id`` (ADR 0005 §1.2, the
same split every other table/column walk in this codebase already resolves through, e.g.
``api/browse_routes.py``). Every function below therefore takes ``assets_by_id`` alongside
``tables`` and resolves columns through it, rather than iterating ``table.columns`` directly.
``ClarificationRecord`` is this repo's frozen dataclass (Phase 1a), not v1's ``pydantic``
model — ``choices``/``raised_by`` are built as tuples, not lists, and there is no
``model_copy``; a record's final shape is built in one constructor call instead.

**Deterministic keyword heuristic, no LLM seam.** v1 optionally rewrote a heuristic template's
question text through a chat model (``_llm_rewrite_questions``) for more natural phrasing. Not
ported: nothing in this port's own spec calls for it, and it is exactly the kind of
"configurability nobody asked for" this project's guidelines warn against — the heuristic's
template text is what ships.

**B and E read the database; the rest read only the corpus.** Both of those categories are
*about* a column's real value vocabulary, so both need values, and both originally took them
from ``ColumnAsset.sample_values`` — a field ``corpus/seed.py``'s live-schema introspection
never populates, so neither could ever fire on a live-seeded corpus (verified: zero candidates
on real ``beer_factory``). :func:`read_observed_values` supplies them instead, through
``serve/fetch.sample_rows`` — statement built as a sqlglot tree, run through ``prepare()``,
one ``path="sample"`` ledger row per attempt. **Not** by restoring ``Connector.sample_values``,
which was deleted rather than fixed for two reasons that both still hold (``ports.py`` around
line 124): it interpolated deliberately-unconstrained identifiers into a string, and it called
``execute`` itself, so it reached the database through no governance layer and wrote no row.

That split is why the reading is its own function and not a branch inside
:func:`generate_candidate_questions`: the generator stays a pure function of
``(tables, assets_by_id, observed_values)`` with nothing to mock, and the one function that
touches a connector is also the one that has ledger rows to hand back to its caller.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from governed_bi.curator.clarifications import (
    ClarificationRecord,
    ElicitationAudience,
    ElicitationCategory,
    ElicitationSeverity,
)

__all__ = [
    "ELICITATION_SOURCE",
    "CATEGORY_PRIORITY",
    "CATEGORY_CLASSIFICATION",
    "MAX_VALUE_READS",
    "generate_candidate_questions",
    "read_observed_values",
    "plain_name",
    "maybe_generate_join_followup",
]

ELICITATION_SOURCE = "elicitation_wizard"

# Conservative, fixed keyword lists — the whole heuristic surface (v1, unchanged). Extending
# coverage later means growing these lists, not changing the fold/ledger contract. A's own list
# moved to ``curator/elicitation_terms.py`` with the category that reads it.
_CATEGORICAL_HINTS: tuple[str, ...] = (
    "country", "region", "category", "channel", "segment", "type", "code",
)
_STATUS_HINTS: tuple[str, ...] = ("status", "rating", "grade", "state")
_SENTINEL_VALUES: frozenset[str] = frozenset(
    {"n/a", "na", "null", "none", "unknown", "unrated", "-1", "pending", "tbd"}
)

CATEGORY_PRIORITY: list[str] = ["A", "C", "E", "B", "D"]

#: ``(severity, audience)`` for each category this generator can actually emit, read off
#: ``detent-ai-setup-wizard-gap-model.md`` § "Gap-type × severity × audience table".
#:
#: Declared once and unpacked by each ``_propose_*`` rather than written five times inline, so
#: "what tier is a B question" has one answer. Severity is a property of a gap *instance*, not of
#: a category (the doc's Part 3 §1) — this table is a per-category floor, and it is only a
#: sufficient answer today because the shipped keyword heuristic cannot see the evidence that
#: would move an instance off it:
#:
#: - **A → T2 / data.** The doc's A row is a hybrid (``BIZ+ENG``) that resolves into *two*
#:   records, and it now does (``curator/elicitation_terms.py``): A-biz on the business tab,
#:   A-eng on the data tab, the second blocked on the first. **Both tiers come from this entry
#:   and only A-eng's audience does** — a pair of questions about one gap is one severity, and
#:   the two audiences are the pair's own definition rather than a per-category fact, so they are
#:   stated at the two templates instead of here. ``A′`` (same gap on an identity/join key) is
#:   T1 and is still invisible: telling it apart needs key detection this generator does not do,
#:   so T2 is the floor, not a claim that no A instance is worse. ``A″`` (only one candidate
#:   column) now suppresses the *business* half, which is the half a forced single choice makes
#:   meaningless.
#: - **B → T2 / business.** ``business`` because the payload is a machine-prepared list of the
#:   real distinct values, which is the whole point: a domain owner must never type a value that
#:   can drift from the stored format. ``B′`` (self-evident values like ``Bottle``/``Can``) is
#:   T4, and nothing here distinguishes it.
#: - **C → T2 / business.** A wrong constant silently changes every count that uses it, and only
#:   a human knows it — no data inspection recovers a fiscal-year start.
#: - **D → T3 / data.** The record this generator mints is
#:   :func:`maybe_generate_join_followup`'s, which fires when a join is *not declared*, i.e. the
#:   doc's ``D′`` row, not its ``D`` row: unanswered, the engine cannot traverse and refuses, so
#:   correctness is not at risk. The doc's T1 ``D`` — two candidate join keys whose values
#:   disagree — has no detector, and when one exists it must set T1 explicitly rather than inherit
#:   this entry.
#: - **E → T2 / business.** Same reasoning as B for the audience (the sentinel is detected, not
#:   typed); only the owner can decide whether ``'unknown'`` rows belong in a regional breakdown.
CATEGORY_CLASSIFICATION: dict[
    ElicitationCategory, tuple[ElicitationSeverity, ElicitationAudience]
] = {
    "A": ("T2", "data"),
    "B": ("T2", "business"),
    "C": ("T2", "business"),
    "D": ("T3", "data"),
    "E": ("T2", "business"),
}

#: B's cardinality ceiling: strictly more than one distinct value, at most this many.
#:
#: Named rather than inlined because the move to a *capped* read made it a claim about
#: ``serve/fetch.SAMPLE_ROWS_MAX_VALUES`` and not just a number. That cap is 20, strictly above
#: this 15, so a column that comes back at the cap is known to have **more** than 15 distinct
#: values rather than merely to have been truncated — the predicate stays exact instead of
#: degrading into an estimate. If the cap ever drops to 15 or below the two become
#: indistinguishable, which is why the relationship is written down here.
_B_MAX_DISTINCT = 15

#: Ceiling on how many governed value reads one :func:`read_observed_values` call issues.
#:
#: ``POST /elicitation/generate`` is an admin-triggered, once-per-onboarding action, offline
#: with respect to every business user's turn, and in the same trust and latency class as the
#: corpus load that precedes it — so paying a governed round trip per candidate column is the
#: right trade, not a cost to engineer away. What it must not do is turn one click into an
#: unbounded number of statements.
#:
#: **Raised from 50 when the keyword gate came off** (:func:`_value_read_columns`). At 50, a
#: read set that is now "every column" would silently truncate ``beer_factory`` at 50 of 93 and
#: report nothing about the rest — and a cost bound that quietly deletes findings is the
#: ``limit_per_category`` mistake wearing a different hat. 800 is chosen against the lake rather
#: than picked round: the widest schema in it is ``works_cycles`` at 703 columns
#: (``information_schema.columns``, measured 2026-08-12), so on every schema that exists here
#: the cap does not bind and the scan is complete. It is still a bound — it stops one click on
#: some future 5 000-column warehouse from becoming 5 000 statements — but it is deliberately
#: above the data, because truncating a scan is worse than paying for it at this cadence. Each
#: read is one ``SELECT DISTINCT c FROM t ORDER BY c LIMIT 20``; 93 of them against real
#: Postgres take under two seconds.
#:
#: A constant rather than a knob, for ``SAMPLE_ROWS_MAX_VALUES``'s own stated reason: nothing on
#: this surface can write a knob, so declaring one would be a control with no writer. The
#: truncation is deterministic (``_live_tables`` sorts by id, columns follow the table's own
#: ``columns`` order), so which columns a capped call reads does not move between runs.
#:
#: A **cost** bound, and the only kind left on this module: the per-category *reporting* cap
#: (``limit_per_category``) is gone, because dropping a finding to fit a quota is what let a T3
#: crowd out a T1 (``curator/gaps.py``'s own module docstring). Bounding round trips per click
#: and bounding what an admin is told are different quantities with different justifications.
MAX_VALUE_READS = 800


def _record_id(scope: str) -> str:
    """A stable id derived from ``scope``, not a sequential counter (v1's ``next_clarification_id``
    allocates ``qNNN`` against the whole ledger; this repo's ledger also carries ``live_chat``
    records keyed by a LangChain ``tool_call_id``, so a counter shared across sources would be
    guessing at a format the other source does not use). Deterministic means calling the
    generator twice for the same candidate always proposes the same id, which is what makes
    filtering by ``existing`` scopes (below) enough for idempotency on its own.
    """
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]
    return f"elicit.{digest}"


def _live_tables(tables: Sequence[Any]) -> list[Any]:
    """``tables``, excluded ones dropped, in a fixed (id-sorted) order — so the proposed set and
    its order are the same on every run regardless of what order the caller's
    ``assets_by_id.values()`` happened to iterate in, and so ``MAX_VALUE_READS``'s truncation
    reads the same columns each time.
    """
    return sorted(
        (t for t in tables if not t.governance.excluded),
        key=lambda t: t.id,
    )


def _columns_of(table: Any, assets_by_id: dict[str, Any]) -> list[Any]:
    """A table's ``ColumnAsset``s, resolved from its ``columns`` id tuple. A dangling id (should
    not happen — the loader derives ``columns`` from the same assets it puts in ``assets_by_id``)
    is skipped rather than raised on, matching ``api/browse_routes.py``'s own defensive read.
    """
    return [c for c in (assets_by_id.get(cid) for cid in table.columns) if c is not None]


def _name_hits(column: Any, hints: tuple[str, ...]) -> bool:
    """Whether ``column``'s physical name contains any of ``hints``. One definition, two readers
    (B's list and E's list), so "which columns does this category care about" has one answer."""
    lowered = column.physical_name.lower()
    return any(hint in lowered for hint in hints)


# ── how a physical name reads to a business owner ───────────────────────────────────────────

#: A lowercase-or-digit immediately followed by an uppercase: the camelCase seam.
_CAMEL_SEAM = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: Everything that is not a letter or a digit — ``_``, ``.``, ``-``, spaces, anything a quoted
#: identifier can legally hold.
_NOT_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def plain_name(physical_name: str) -> str:
    """A physical identifier rendered as words, for a question a business owner reads.

    ``content_rating`` -> ``content rating``; ``Sentiment_Polarity`` -> ``Sentiment Polarity``;
    ``in_dosen_erh_ltlich`` -> ``in dosen erh ltlich``. Deliberately **not** a translation or a
    prettifier: it drops the separators and the case seams that make a token read as an
    identifier and changes nothing else, so the words an admin sees are the customer's own
    vocabulary rather than a guess at what they meant.

    This is what makes the business templates pass :func:`enforce_audience_language` by
    construction rather than by review. The output holds only letters, digits and spaces, so it
    cannot contain a dotted path, a snake_case token or a camelCase run — the three shapes
    ``serve/schema_term_guard.find_schema_leak`` looks for. The guard is still applied at
    runtime, because "the templates are careful" is a convention and the next edit to one is not
    bound by it.

    An all-punctuation name (nothing survives the split) falls back to itself. That is the one
    input this cannot render, and returning the original keeps the question about a real object
    instead of about nothing — the guard is what catches it if the original is identifier-shaped.
    """
    words = [w for w in _NOT_ALNUM.split(_CAMEL_SEAM.sub(" ", physical_name)) if w]
    return " ".join(words) or physical_name



def _value_read_columns(
    tables: Sequence[Any], assets_by_id: dict[str, Any]
) -> list[tuple[Any, Any]]:
    """``(table, column)`` for every column a value read is issued for, in a fixed order.

    **Every column, and the keyword gate that used to be here is gone.** It was the union of B's
    and E's name lists, which was a defensible cost saving for exactly as long as every reader of
    the values was itself name-gated. :func:`_propose_s6` is not: the design doc's whole point
    about the S6 row is that the signal is the *value*, and a read set chosen by column name
    cannot deliver a value-driven detector — ``restaurant.geografisch.region = 'unknown'`` was
    missed because ``region`` is not in ``_STATUS_HINTS``, and no amount of care inside the
    detector recovers a value nobody read.

    The cost moves from "proportional to the keyword hits" to "proportional to the width of the
    schema", which is what :data:`MAX_VALUE_READS` now bounds and why it was raised with this
    change rather than after it. B and E are unaffected in *output*: both still apply their own
    name gate to the columns they propose about, so widening the read set can only give them
    values they would have ignored.
    """
    return [
        (table, column)
        for table in _live_tables(tables)
        for column in _columns_of(table, assets_by_id)
    ]



def read_observed_values(
    tables: Sequence[Any],
    assets_by_id: dict[str, Any],
    *,
    connector: Any,
    corpus: Any,
    policy: Any,
    max_reads: int = MAX_VALUE_READS,
) -> tuple[dict[str, tuple[str, ...]], tuple[Any, ...]]:
    """``({column_id: distinct values}, ledger rows)`` for the columns B and E can use.

    Every read goes through ``serve/fetch.sample_rows``: the same governed executor path the
    live agent's own ``sample_rows`` tool takes, which builds the statement as a syntax tree
    (``distinct_values_statement``), runs it through ``prepare()``, and returns an
    ``attempt_record`` with ``path="sample"``. Reusing that function rather than assembling
    ``distinct_values_statement`` + ``prepare`` + ``execute`` here is deliberate: a second copy
    of that body would be a second answer to "what does a governed value read check", and this
    caller needs *none* of the checks relaxed.

    ``bounds`` licenses exactly the one table the column being sampled belongs to, and nothing
    else. There is no retrieval to derive a licensed set from — an admin asked for a scan of the
    semantic layer, not a turn — so the narrowest bound that can name the column at all is the
    honest one, and it also keeps ``spellings_for`` scoped to a single table (a corpus-wide
    fold map makes ``name``/``id``/``code`` ambiguous and would refuse almost everything).

    **A refusal skips the column; it never routes around it.** ``check()`` still runs every
    layer, so an ``excluded`` or ``suspect``-flagged column (under ``hard_block_suspect``) is
    refused at COLUMNS and simply gets no entry in the returned mapping — which makes it not a
    candidate. The refusal's ledger row is still returned, because a refused attempt is a
    governance decision the audit trail owes a row exactly as much as a passing one does. The
    same is true of a driver failure and of a session with no connector at all: ``sample_rows``
    already decides what each of those is, and re-deciding any of them here would be the
    second-source-of-truth this module's own docstring warns about.

    ``sample_rows`` returns its payload as JSON because its other caller is a language model.
    Parsing it back is the cost of having one implementation of the read instead of two.
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
    from governed_bi.serve.fetch import SAMPLE_ROWS_MAX_VALUES, sample_rows

    observed: dict[str, tuple[str, ...]] = {}
    ledger: list[Any] = []
    for table, column in _value_read_columns(tables, assets_by_id)[: max(0, int(max_reads))]:
        payload, delivered, attempt = sample_rows(
            column.id,
            # The cap, not a smaller number: one read serves both categories, and 20 is above
            # B's own ceiling of 15, which is what keeps its predicate exact (see
            # :data:`_B_MAX_DISTINCT`).
            limit=SAMPLE_ROWS_MAX_VALUES,
            bounds=ToolBounds(licensed=frozenset({table.id})),
            assets=assets_by_id,
            connector=connector,
            corpus=corpus,
            policy=policy,
        )
        if attempt is not None:
            ledger.append(attempt)
        if not delivered:
            continue
        values = json.loads(payload).get("values") or ()
        observed[column.id] = tuple(str(v) for v in values if v is not None)
    return observed, tuple(ledger)


def generate_candidate_questions(
    tables: Sequence[Any],
    assets_by_id: dict[str, Any],
    *,
    observed_values: Mapping[str, tuple[str, ...]] | None = None,
    cardinalities: Mapping[str, Any] | None = None,
) -> list[ClarificationRecord]:
    """Propose every category-tagged candidate question the keyword gates find.

    **No reporting cap.** ``limit_per_category=3`` is gone, and the reason is the owner's
    2026-08-12 decision as ``curator/gaps.py``'s module docstring records it: "list ALL gaps,
    don't truncate; stratify by severity so the admin can stop at any tier". A per-category quota
    is the mechanism that makes a T1 finding vanish because three T3s were generated first — it
    drops a *finding*, silently, and nothing downstream can tell that it did. Cost caps stay
    (:data:`MAX_VALUE_READS` here, ``gaps.MAX_PAIR_COMPARISONS`` there), because how many
    governed statements one admin click issues is a different quantity: it bounds the round trips,
    never the report, and it truncates in a stated order so what it does cost is visible.

    **Every candidate the gates find, including ones the ledger already holds.** The scope
    idempotency filter used to be the last line of this function and is now
    ``curator/scan_report.diff_scan_against_ledger``'s first, for two reasons that are really
    one. It was written twice — here for the keyword half and in
    ``api/curation_routes.py::elicitation_generate`` for the structural half, because
    ``curator/gaps.py`` has no ``existing`` parameter — so "already proposed" had two
    implementations over one ledger. And the re-run report needs the *unfiltered* set: telling an
    admin that 16 questions are carried forward from an earlier scan is only possible if
    something still knows this scan re-derived them. A generator that pre-filters its own output
    destroys exactly the information the report is made of.

    ``observed_values`` is :func:`read_observed_values`'s mapping, keyed by column id. B and E
    are the only categories that use it, and a column with no entry — never read, or read and
    refused — is not a candidate for either. Omitting it entirely is therefore a corpus-only
    scan that proposes A and C and nothing else, which is the honest result for a caller with
    no connector to read through rather than a reason to fall back to
    ``ColumnAsset.sample_values`` (empty on every live-seeded corpus, so a fallback there would
    be a second, silently-worse source for the same fact).

    ``cardinalities`` is ``curator/elicitation_terms.read_term_cardinalities``' mapping, and only
    A reads it. Omitting it costs A its grain sentence and its row counts — the questions are
    still asked and still name their candidates, they just say less, which is the honest result
    for a caller with nothing to count through. Imported inside the function because
    ``elicitation_terms`` imports this module's own helpers; the cycle is broken here rather than
    there, where the shared helpers are.
    """
    from governed_bi.curator.elicitation_terms import propose_term_questions

    live_tables = _live_tables(tables)
    observed = observed_values or {}

    candidates: list[ClarificationRecord] = []
    candidates += propose_term_questions(
        live_tables, assets_by_id, observed_values=observed, cardinalities=cardinalities
    )
    candidates += _propose_c(live_tables, assets_by_id)
    candidates += _propose_e(live_tables, assets_by_id, observed)
    # S6 after E, and it reads E's output: the two ask one question, and the only difference is
    # what made the column a candidate. A column E already covered gets no second card.
    candidates += _propose_s6(live_tables, assets_by_id, observed, covered=candidates)
    candidates += _propose_b(live_tables, assets_by_id, observed)
    return candidates


#: C's fixed choice list: month number -> "N - Name". Built once; identical for every schema.
_FISCAL_MONTH_NAMES: tuple[str, ...] = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _is_date_like(column: Any) -> bool:
    """Whether ``column`` is a date/datetime column — ``logical_type`` when set, else a
    ``physical_type`` substring check.

    **Real-corpus gap, found live against ``beer_factory``** (not in v1, whose ``Column``
    fixture always carried a populated ``logical_type``): ``corpus/seed.py``'s live-schema
    introspection path (``Session.from_live_schema``) never sets ``ColumnAsset.logical_type`` at
    all — only ``physical_type``, the raw DB type string (e.g. ``"date"``, ``"timestamp"``).
    Without this fallback, C could never fire against any freshly-seeded, uncurated corpus,
    which is most of them; ``logical_type`` is presumably filled in by a later curation pass
    this port does not otherwise depend on.
    """
    from governed_bi.corpus.schema import LogicalType

    if column.logical_type is not None:
        return column.logical_type in (LogicalType.date, LogicalType.datetime)
    physical = (column.physical_type or "").lower()
    return "date" in physical or "time" in physical


def _propose_c(tables: Sequence[Any], assets_by_id: dict[str, Any]) -> list[ClarificationRecord]:
    """C: business-rule constants, only proposed when the schema plausibly needs them (a
    date/datetime column exists) — collected with A per the design doc's "collect together"
    finding."""
    has_date_column = any(
        _is_date_like(column)
        for table in tables
        for column in _columns_of(table, assets_by_id)
    )
    if not has_date_column:
        return []
    severity, audience = CATEGORY_CLASSIFICATION["C"]
    scope = "elicitation:rule:fiscal_year_start"
    return [
        ClarificationRecord(
            id=_record_id(scope),
            scope=scope,
            question="What month does your fiscal year start? (enter 1-12, 1 = January)",
            category="C",
            ui_modality="numeric",
            severity=severity,
            audience=audience,
            choices=tuple(
                {"id": str(i), "label": f"{i} - {name}"}
                for i, name in enumerate(_FISCAL_MONTH_NAMES, start=1)
            ),
            allow_freeform=True,
            raised_by=("elicitation_wizard",),
            source=ELICITATION_SOURCE,
        )
    ]


def _propose_e(
    tables: Sequence[Any],
    assets_by_id: dict[str, Any],
    observed_values: Mapping[str, tuple[str, ...]],
) -> list[ClarificationRecord]:
    """E: for a status/rating-like column whose **observed** values include a null-like
    sentinel, ask whether to exclude it by default.

    **Semantics shifted with the value source, in E's favour.** The old gate scanned
    ``ColumnAsset.sample_values``, an unordered sample of unstated size that in practice was
    always empty. The new one scans the first ``SAMPLE_ROWS_MAX_VALUES`` distinct values in
    ``ORDER BY`` order, so a sentinel that sorts past that cap on a high-cardinality column is
    missed. That is a real limit and worth stating, but it is strictly better than what it
    replaces on both counts: the values are real, and *which* values are looked at is
    deterministic rather than whatever a sampler happened to have kept. It is also barely
    reachable in practice — this gate only fires on status/rating-like columns, whose whole
    point is a small closed vocabulary, and the sentinels it looks for (``n/a``, ``null``,
    ``pending``, ``-1``, …) sort early in most of them.

    **The "not yet rated" gloss is gone**, and for the same reason B's "(e.g. 'domestic')" is:
    it was v1's worked example for a *rating* column, and :data:`_STATUS_HINTS` admits
    ``status``, ``state`` and ``grade`` too, so a live ``content_rating`` question and a
    ``review_status`` one both asked whether the value "means 'not yet rated'". The detector
    never measured that; what it measured is that the value is null-like, and the question now
    says only that. It also stopped asking two questions ("is there a value…? should it be…?")
    behind one two-option widget that could only answer the second.
    """
    severity, audience = CATEGORY_CLASSIFICATION["E"]
    out: list[ClarificationRecord] = []
    for table in tables:
        for column in _columns_of(table, assets_by_id):
            if not _name_hits(column, _STATUS_HINTS):
                continue
            sentinel = _sentinel_in(observed_values.get(column.id) or ())
            if sentinel is None:
                continue
            scope = f"elicitation:exclusion:{table.physical_name}.{column.physical_name}"
            out.append(
                ClarificationRecord(
                    id=_record_id(scope),
                    scope=scope,
                    question=(
                        f"In your {plain_name(table.physical_name)} data, "
                        f"{plain_name(column.physical_name)!r} is sometimes recorded as "
                        f"{sentinel!r}. If that means the information is missing rather than a "
                        "real value, should those rows be left out of counts and averages?"
                    ),
                    category="E",
                    ui_modality="checkbox",
                    severity=severity,
                    audience=audience,
                    choices=_exclusion_choices(column, sentinel),
                    allow_freeform=True,
                    target_table=table.physical_name,
                    target_column=column.physical_name,
                    raised_by=("elicitation_wizard",),
                    source=ELICITATION_SOURCE,
                )
            )
    return out


def _exclusion_choices(column: Any, sentinel: str) -> tuple[Mapping[str, str], ...]:
    """E's and S6's two options. One definition, because the two questions differ in *what made
    the column a candidate*, never in what an answer to them means.

    Both labels name the column and the value, in business words. That is not decoration: the
    composed corpus fact is built from the picked label
    (``curator/elicitation_answers.py``), so a label that said only "yes" would fold into the
    semantic layer as an exclusion of nothing in particular.
    """
    field = plain_name(column.physical_name)
    return (
        {"id": "exclude", "label": f"Leave out the rows where {field} is {sentinel!r}"},
        {"id": "include", "label": f"Count them; {sentinel!r} is a real value here"},
    )


def _sentinel_in(values: Sequence[str]) -> str | None:
    """The first null-like value in ``values``, if any. One definition, read by E and by S6.

    Blank counts, and it is the one addition to :data:`_SENTINEL_VALUES`: an empty string in a
    grouping column is a bucket with no name, which is the same gap as ``'unknown'`` and is the
    only sentinel shape that is not a word in some language.
    """
    return next((v for v in values if not v.strip() or v.strip().lower() in _SENTINEL_VALUES), None)


def _groups_or_averages(column: Any, values: Sequence[str]) -> bool:
    """Whether this column is one an answer would ``GROUP BY`` or ``AVG`` — on evidence.

    Two shapes, and neither is a name test. **Groupable**: the value read came back *below*
    ``SAMPLE_ROWS_MAX_VALUES``, so what came back is the column's whole vocabulary rather than a
    truncation of it, and that vocabulary is small. The exactness matters for the same reason it
    does for :data:`_B_MAX_DISTINCT` — at the cap the count stops being a count and becomes a
    lower bound, and a detector that treats the two alike is guessing. **Averageable**: a numeric
    physical type, where the classic sentinel is ``-1`` and it sorts first, so the capped read
    sees it whenever it is there.
    """
    from governed_bi.serve.fetch import SAMPLE_ROWS_MAX_VALUES

    if _is_numeric(column):
        return True
    return 1 < len(values) < SAMPLE_ROWS_MAX_VALUES


def _is_numeric(column: Any) -> bool:
    from governed_bi.curator.gap_signals import type_class

    return type_class(column) == "numeric"


def _propose_s6(
    tables: Sequence[Any],
    assets_by_id: dict[str, Any],
    observed_values: Mapping[str, tuple[str, ...]],
    *,
    covered: Sequence[ClarificationRecord],
) -> list[ClarificationRecord]:
    """S6: a sentinel value in a grouping or averaging column, found with **no name gate at all**.

    ``detent-ai-setup-wizard-gap-model.md``'s S6 row, and its own summary of why E is not enough:
    "E requires the column *name* to match ``_STATUS_HINTS`` AND a sentinel value; ``region``
    fails the name test. E's real signal is the value, not the name; ANDing the two destroys
    recall." So this is E with that conjunct removed and a measured one put in its place — the
    column has to be one an answer would actually group or average by
    (:func:`_groups_or_averages`), because "there is a null-like value in here somewhere" is not
    a gap on a free-text comment field.

    **Its prerequisite was a read-set change, not a detector change.** A value-driven detector
    over a keyword-gated read set is still keyword-gated, one layer down; ``region``'s values
    were never fetched. :func:`_value_read_columns` now reads every column, which is what makes
    this reachable and what moved :data:`MAX_VALUE_READS`.

    **A column E already asked about gets no second card.** The two rows in the doc's table are
    one question with two provenances, so ``covered`` is E's output and this skips anything in
    it — measured on ``app_store``, where all three ``*content_rating*`` columns hold
    ``'Unrated'`` and E's name gate already reaches every one of them.

    **What this still cannot see, stated rather than implied.** SQL ``NULL`` itself is invisible:
    ``distinct_values_statement`` filters ``IS NOT NULL``, so the doc's ``location.State`` NULL
    case needs a null count this does not read. And :data:`_SENTINEL_VALUES` is an English word
    list — the same failure ``curator/gaps.py`` exists because of — so this finds three columns
    on ``restaurant`` and **zero** on German ``beer_factory``. Removing the *name* gate is what
    the doc asked for and is done; the *value* vocabulary is still a language, and
    ``orders.status = 'cancelled'`` is missed for that reason and not for E's.
    """
    severity, audience = CATEGORY_CLASSIFICATION["E"]
    already = {(r.target_table, r.target_column) for r in covered if r.category == "E"}
    out: list[ClarificationRecord] = []
    for table in tables:
        for column in _columns_of(table, assets_by_id):
            if (table.physical_name, column.physical_name) in already:
                continue
            values = observed_values.get(column.id) or ()
            sentinel = _sentinel_in(values)
            if sentinel is None or not _groups_or_averages(column, values):
                continue
            scope = f"elicitation:sentinel:{table.physical_name}.{column.physical_name}"
            out.append(
                ClarificationRecord(
                    id=_record_id(scope),
                    scope=scope,
                    question=(
                        f"Totals in your {plain_name(table.physical_name)} data get broken down "
                        f"by {plain_name(column.physical_name)!r}, and one of its values is "
                        f"{sentinel!r}. If that means the information is missing rather than a "
                        "real category, should those rows be left out?"
                    ),
                    category="E",
                    ui_modality="checkbox",
                    severity=severity,
                    audience=audience,
                    choices=_exclusion_choices(column, sentinel),
                    allow_freeform=True,
                    target_table=table.physical_name,
                    target_column=column.physical_name,
                    raised_by=("elicitation_wizard",),
                    source=ELICITATION_SOURCE,
                )
            )
    return out


def _propose_b(
    tables: Sequence[Any],
    assets_by_id: dict[str, Any],
    observed_values: Mapping[str, tuple[str, ...]],
) -> list[ClarificationRecord]:
    """B: for a small-cardinality categorical column, a checklist of the actual distinct values
    the database returned (:func:`read_observed_values`, through the governed sample path).

    The cardinality window is unchanged and, unlike E's sentinel gate, exactly as strict as it
    was: ``SELECT DISTINCT … LIMIT 20`` returns ``min(cardinality, 20)`` rows, and 20 is above
    :data:`_B_MAX_DISTINCT`, so 16 or more rows back means the column really has more than 15
    distinct values and fewer means the count is exact.

    **The question asks for the group's name, which v1's never did.** v1's template ended
    "...as one group when a business user asks about it (e.g. 'domestic')?" — the parenthetical
    was the only thing naming *which* grouping, and it was a worked example written for a country
    column that shipped unchanged onto every column the gate admits (measured live: a Free/Paid
    column asked about "domestic"). Without it the question has no referent at all, which is also
    why the composed answer used to read "these values count as the grouping asked about" when no
    grouping had been asked about. So the ask is now "check the ones that belong together, and
    say what you call them": the checklist carries the values and the freeform carries the term,
    and ``curator/elicitation_answers.py`` composes a fact out of whichever arrive.
    """
    severity, audience = CATEGORY_CLASSIFICATION["B"]
    out: list[ClarificationRecord] = []
    for table in tables:
        for column in _columns_of(table, assets_by_id):
            if not _name_hits(column, _CATEGORICAL_HINTS):
                continue
            values = sorted({v for v in observed_values.get(column.id) or () if v.strip()})
            if not (1 < len(values) <= _B_MAX_DISTINCT):
                continue
            scope = f"elicitation:valuemap:{table.physical_name}.{column.physical_name}"
            out.append(
                ClarificationRecord(
                    id=_record_id(scope),
                    scope=scope,
                    question=(
                        f"In your {plain_name(table.physical_name)} data, do any of these "
                        f"{plain_name(column.physical_name)!r} values mean the same thing in "
                        "everyday language? Check the ones that belong together, and say what "
                        "you call them."
                    ),
                    category="B",
                    ui_modality="checklist",
                    severity=severity,
                    audience=audience,
                    choices=tuple({"id": v, "label": v} for v in values),
                    allow_freeform=True,
                    target_table=table.physical_name,
                    target_column=column.physical_name,
                    raised_by=("elicitation_wizard",),
                    source=ELICITATION_SOURCE,
                )
            )
    return out


def maybe_generate_join_followup(rec: ClarificationRecord, picked_choice_id: str) -> ClarificationRecord | None:
    """After an A-category answer is folded, check whether the picked column lives on a
    different table than schema-inference expected (``rec.target_table`` — the alphabetically-
    first candidate table offered when the A question was generated).

    Returns a new, open D-category follow-up record when they differ, else ``None``. D never
    gets its own standalone question set (:func:`generate_candidate_questions` never proposes
    one) — this is the only path that creates one, and it is always tied to the specific A
    answer that triggered it.

    **Keyed on the A-eng scope, not on ``category == "A"``.** Since the A pair landed, category A
    carries four question shapes with a ``table.column`` choice id between them, and only one of
    them binds a term to a column: A-biz's pick is a *meaning* whose id happens to name the
    column its description was derived from, and minting a join question off it would be
    proposing a join before the DBA has confirmed there is one to propose. ``gaps.py``'s own
    category-A records (describe-this-table, describe-these-columns, this-column-is-flagged)
    are excluded by the same test rather than by the absence of a dotted choice id.
    """
    from governed_bi.curator.elicitation_terms import A_ENG_SCOPE_PREFIX

    if not rec.scope.startswith(A_ENG_SCOPE_PREFIX) or not rec.target_table:
        return None
    if "." not in picked_choice_id:
        return None
    picked_table, picked_column = picked_choice_id.split(".", 1)
    if picked_table == rec.target_table:
        return None
    term = rec.scope.rsplit(":", 1)[-1]
    severity, audience = CATEGORY_CLASSIFICATION["D"]
    scope = f"elicitation:join:{rec.target_table}:{picked_table}"
    return ClarificationRecord(
        id=_record_id(scope),
        scope=scope,
        question=(
            f"'{term}' maps to `{picked_table}.{picked_column}`, on a different table than "
            f"expected (`{rec.target_table}`). How do `{rec.target_table}` and `{picked_table}` "
            "join (e.g. which columns)?"
        ),
        category="D",
        ui_modality=None,
        severity=severity,
        audience=audience,
        choices=None,
        allow_freeform=True,
        target_table=picked_table,
        target_column=picked_column,
        raised_by=("elicitation_wizard:auto",),
        source=ELICITATION_SOURCE,
    )
