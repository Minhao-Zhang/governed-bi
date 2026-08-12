"""Phase 2 Setup Wizard: proactive admin onboarding elicitation (UtkuAI v1, ported).

Unlike the reactive ``ask_user`` live-chat clarification (``serve/tools.py`` — fires mid-turn
when the live agent is uncertain), this scans an already-known schema **before** any business
user ever asks a question and proposes a small, conservative set of category-tagged candidate
questions for an admin to answer once. This module only decides WHAT to ask; answers reuse the
exact same :class:`~governed_bi.curator.clarifications.ClarificationRecord` ledger + fold
pipeline (``api/curation_routes.py::answer_clarification_route`` ->
``curator/clarification.py::fold_ledger_answer_into_corpus``) as every other clarification
source — no new storage path.

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
    "compose_elicitation_answer_text",
    "maybe_generate_join_followup",
]

ELICITATION_SOURCE = "elicitation_wizard"

# Conservative, fixed keyword lists — the whole heuristic surface (v1, unchanged). Extending
# coverage later means growing these lists, not changing the fold/ledger contract.
_AMBIGUOUS_TERMS: tuple[str, ...] = (
    "revenue", "cost", "profit", "total", "amount", "price", "balance", "value",
)
_CATEGORICAL_HINTS: tuple[str, ...] = (
    "country", "region", "category", "channel", "segment", "type", "code",
)
_STATUS_HINTS: tuple[str, ...] = ("status", "rating", "grade", "state")
_SENTINEL_VALUES: frozenset[str] = frozenset(
    {"n/a", "na", "null", "none", "unknown", "unrated", "-1", "pending", "tbd"}
)

CATEGORY_PRIORITY: list[str] = ["A", "C", "E", "B", "D"]

#: ``(severity, audience)`` for each category this generator can actually emit, read off
#: ``utku-ai-setup-wizard-gap-model.md`` § "Gap-type × severity × audience table".
#:
#: Declared once and unpacked by each ``_propose_*`` rather than written five times inline, so
#: "what tier is a B question" has one answer. Severity is a property of a gap *instance*, not of
#: a category (the doc's Part 3 §1) — this table is a per-category floor, and it is only a
#: sufficient answer today because the shipped keyword heuristic cannot see the evidence that
#: would move an instance off it:
#:
#: - **A → T2 / data.** The doc's A row is a hybrid (``BIZ+ENG``) that resolves into *two*
#:   records, and the question as shipped is the engineering one: its choices are bare
#:   ``table.column`` identifiers, which is exactly what the business half must not contain. Its
#:   refinements are invisible here — ``A′`` (same gap on an identity/join key) is T1 and ``A″``
#:   (only one candidate column) should not be generated at all, and telling either apart needs
#:   the key/disagreement detection a later phase builds. T2 is the floor, not a claim that no
#:   A instance is worse.
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
#: unbounded number of statements: only keyword-gated columns are read (see
#: :func:`_value_gated_columns`), but a wide enough schema can have hundreds of ``*_code`` /
#: ``*_type`` columns, and B and E each keep at most ``limit_per_category`` candidates anyway.
#:
#: A constant rather than a knob, for ``SAMPLE_ROWS_MAX_VALUES``'s own stated reason: nothing on
#: this surface can write a knob, so declaring one would be a control with no writer. The
#: truncation is deterministic (``_live_tables`` sorts by id, columns follow the table's own
#: ``columns`` order), so which columns a capped call reads does not move between runs.
MAX_VALUE_READS = 50


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
    """``tables``, excluded ones dropped, in a fixed (id-sorted) order — so which candidates a
    per-category ``limit`` keeps is deterministic regardless of what order the caller's
    ``assets_by_id.values()`` happened to iterate in.
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
    """Whether ``column``'s physical name contains any of ``hints``. One definition, three
    readers (B's list, E's list, and the union :func:`_value_gated_columns` reads values for) —
    so "which columns does this category care about" cannot drift from "which columns get a
    query issued for them"."""
    lowered = column.physical_name.lower()
    return any(hint in lowered for hint in hints)


def _value_gated_columns(
    tables: Sequence[Any], assets_by_id: dict[str, Any]
) -> list[tuple[Any, Any]]:
    """``(table, column)`` for every column B or E could possibly ask about, in a fixed order.

    The union of the two keyword gates, evaluated *before* any statement is built, because that
    is what keeps the cost of a generate call proportional to the columns a category might use
    rather than to the width of the schema.
    """
    return [
        (table, column)
        for table in _live_tables(tables)
        for column in _columns_of(table, assets_by_id)
        if _name_hits(column, _CATEGORICAL_HINTS) or _name_hits(column, _STATUS_HINTS)
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
    from governed_bi.govern.bounds import ToolBounds
    from governed_bi.serve.fetch import SAMPLE_ROWS_MAX_VALUES, sample_rows

    observed: dict[str, tuple[str, ...]] = {}
    ledger: list[Any] = []
    for table, column in _value_gated_columns(tables, assets_by_id)[: max(0, int(max_reads))]:
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
    existing: Sequence[ClarificationRecord] = (),
    limit_per_category: int = 3,
    observed_values: Mapping[str, tuple[str, ...]] | None = None,
) -> list[ClarificationRecord]:
    """Propose a conservative set of category-tagged candidate questions.

    ``existing`` is the ledger's current records — used only to make this idempotent: a
    candidate whose ``scope`` already exists among prior ``source="elicitation_wizard"``
    records is dropped before it is returned. Returns only the newly proposed records (the
    caller appends them to the ledger); ``existing`` itself is never mutated or re-returned.

    ``observed_values`` is :func:`read_observed_values`'s mapping, keyed by column id. B and E
    are the only categories that use it, and a column with no entry — never read, or read and
    refused — is not a candidate for either. Omitting it entirely is therefore a corpus-only
    scan that proposes A and C and nothing else, which is the honest result for a caller with
    no connector to read through rather than a reason to fall back to
    ``ColumnAsset.sample_values`` (empty on every live-seeded corpus, so a fallback there would
    be a second, silently-worse source for the same fact).
    """
    live_tables = _live_tables(tables)
    existing_scopes = {r.scope for r in existing if r.source == ELICITATION_SOURCE}
    observed = observed_values or {}

    candidates: list[ClarificationRecord] = []
    candidates += _propose_a(live_tables, assets_by_id, limit_per_category)
    candidates += _propose_c(live_tables, assets_by_id, limit_per_category)
    candidates += _propose_e(live_tables, assets_by_id, limit_per_category, observed)
    candidates += _propose_b(live_tables, assets_by_id, limit_per_category, observed)
    return [c for c in candidates if c.scope not in existing_scopes]


def _propose_a(tables: Sequence[Any], assets_by_id: dict[str, Any], limit: int) -> list[ClarificationRecord]:
    """A: for each ambiguous term found in >=1 column name, a column-picker question over every
    matching ``table.column`` candidate."""
    severity, audience = CATEGORY_CLASSIFICATION["A"]
    out: list[ClarificationRecord] = []
    for term in _AMBIGUOUS_TERMS:
        matches: list[tuple[str, str]] = []
        for table in tables:
            for column in _columns_of(table, assets_by_id):
                if term in column.physical_name.lower():
                    matches.append((table.physical_name, column.physical_name))
        if not matches:
            continue
        matches.sort()
        choices = tuple({"id": f"{tbl}.{col}", "label": f"{tbl}.{col}"} for tbl, col in matches)
        scope = f"elicitation:term:{term}"
        out.append(
            ClarificationRecord(
                id=_record_id(scope),
                scope=scope,
                question=f"When you say '{term}', which table/column does that map to?",
                category="A",
                ui_modality="column_picker",
                severity=severity,
                audience=audience,
                choices=choices,
                allow_freeform=True,
                target_table=matches[0][0],  # "expected" table for the D heuristic
                raised_by=("elicitation_wizard",),
                source=ELICITATION_SOURCE,
            )
        )
        if len(out) >= limit:
            break
    return out


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


def _propose_c(tables: Sequence[Any], assets_by_id: dict[str, Any], limit: int) -> list[ClarificationRecord]:
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
    ][:limit]


def _propose_e(
    tables: Sequence[Any],
    assets_by_id: dict[str, Any],
    limit: int,
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
    """
    severity, audience = CATEGORY_CLASSIFICATION["E"]
    out: list[ClarificationRecord] = []
    for table in tables:
        for column in _columns_of(table, assets_by_id):
            if not _name_hits(column, _STATUS_HINTS):
                continue
            sentinel = next(
                (
                    v
                    for v in observed_values.get(column.id) or ()
                    if v.strip().lower() in _SENTINEL_VALUES
                ),
                None,
            )
            if sentinel is None:
                continue
            scope = f"elicitation:exclusion:{table.physical_name}.{column.physical_name}"
            out.append(
                ClarificationRecord(
                    id=_record_id(scope),
                    scope=scope,
                    question=(
                        f"Is there a value in `{table.physical_name}.{column.physical_name}` "
                        f"that means 'not yet rated' (seen: {sentinel!r})? Should it be "
                        "excluded from analysis by default?"
                    ),
                    category="E",
                    ui_modality="checkbox",
                    severity=severity,
                    audience=audience,
                    choices=(
                        {
                            "id": "exclude",
                            "label": f"Exclude rows where {column.physical_name} = {sentinel!r}",
                        },
                        {"id": "include", "label": "Include them"},
                    ),
                    allow_freeform=True,
                    target_table=table.physical_name,
                    target_column=column.physical_name,
                    raised_by=("elicitation_wizard",),
                    source=ELICITATION_SOURCE,
                )
            )
            if len(out) >= limit:
                return out
    return out


def _propose_b(
    tables: Sequence[Any],
    assets_by_id: dict[str, Any],
    limit: int,
    observed_values: Mapping[str, tuple[str, ...]],
) -> list[ClarificationRecord]:
    """B: for a small-cardinality categorical column, a checklist of the actual distinct values
    the database returned (:func:`read_observed_values`, through the governed sample path).

    The cardinality window is unchanged and, unlike E's sentinel gate, exactly as strict as it
    was: ``SELECT DISTINCT … LIMIT 20`` returns ``min(cardinality, 20)`` rows, and 20 is above
    :data:`_B_MAX_DISTINCT`, so 16 or more rows back means the column really has more than 15
    distinct values and fewer means the count is exact.
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
                        f"Which values of `{table.physical_name}.{column.physical_name}` "
                        "should count together as one group when a business user asks about "
                        "it (e.g. 'domestic')? Check all that apply."
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
            if len(out) >= limit:
                return out
    return out


def compose_elicitation_answer_text(
    rec: ClarificationRecord,
    *,
    choice_id: str | None = None,
    choice_ids: list[str] | None = None,
    freeform: str | None = None,
) -> str:
    """Build the self-contained sentence a category-tagged answer folds as (v1, ported
    unchanged in behavior).

    A bare picked-choice label (e.g. ``"sales.total_amount"``) loses the term/rule context that
    made the label meaningful once it is written as a corpus note's summary — this reconstructs
    that context using the question's ``category``/``scope``/``target_table``/``target_column``.
    Written into ``ClarificationRecord.answer`` at answer time
    (``api/curation_routes.py::answer_clarification_route``); from then on
    ``curator/clarifications.py::resolve_answer_text`` returns it verbatim for a category-tagged
    record (its own "category is not None" bypass).

    Every category accepts either a picked choice or freeform text (a user may answer either
    way) — each branch below handles whichever one was actually supplied, not just the
    modality the question was designed around, or the other input silently vanishes instead of
    folding (the exact "choice-picked answer disappears" bug class this codebase has hit and
    fixed before, just for the opposite input shape).
    """
    choices_by_id = {c["id"]: c["label"] for c in (rec.choices or ())}
    freeform = (freeform or "").strip()

    if rec.category == "A":
        term = rec.scope.rsplit(":", 1)[-1]
        if choice_id is not None:
            label = choices_by_id.get(choice_id, choice_id)
            return f"'{term}' maps to {label}."
        if freeform:
            return f"'{term}' maps to {freeform}."
        return ""
    if rec.category == "C":
        if freeform:
            return f"Fiscal year starts in month {freeform}."
        if choice_id is not None:
            label = choices_by_id.get(choice_id, choice_id)
            return f"Fiscal year starts in month {label}."
        return ""
    if rec.category == "E":
        if choice_id == "exclude":
            label = choices_by_id.get(choice_id, "")
            return f"{label} — apply this exclusion by default."
        if choice_id == "include":
            return f"{rec.target_table}.{rec.target_column}: no default exclusion (include all values)."
        if freeform:
            return f"{rec.target_table}.{rec.target_column}: {freeform}"
        return ""
    if rec.category == "B":
        selected = [choices_by_id.get(cid, cid) for cid in (choice_ids or ())]
        if selected:
            return (
                f"For {rec.target_table}.{rec.target_column}, these values count as the "
                f"grouping asked about: {', '.join(selected)}."
            )
        if freeform:
            return f"For {rec.target_table}.{rec.target_column}, the grouping asked about: {freeform}"
        return ""
    if rec.category == "D":
        return freeform
    return freeform or choices_by_id.get(choice_id or "", "")


def maybe_generate_join_followup(rec: ClarificationRecord, picked_choice_id: str) -> ClarificationRecord | None:
    """After an A-category answer is folded, check whether the picked column lives on a
    different table than schema-inference expected (``rec.target_table`` — the alphabetically-
    first candidate table offered when the A question was generated).

    Returns a new, open D-category follow-up record when they differ, else ``None``. D never
    gets its own standalone question set (:func:`generate_candidate_questions` never proposes
    one) — this is the only path that creates one, and it is always tied to the specific A
    answer that triggered it.
    """
    if rec.category != "A" or not rec.target_table:
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
