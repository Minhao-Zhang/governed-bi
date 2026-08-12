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
"""

from __future__ import annotations

import hashlib
from typing import Any, Sequence

from governed_bi.curator.clarifications import ClarificationRecord

__all__ = [
    "ELICITATION_SOURCE",
    "CATEGORY_PRIORITY",
    "generate_candidate_questions",
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


def generate_candidate_questions(
    tables: Sequence[Any],
    assets_by_id: dict[str, Any],
    *,
    existing: Sequence[ClarificationRecord] = (),
    limit_per_category: int = 3,
) -> list[ClarificationRecord]:
    """Propose a conservative set of category-tagged candidate questions.

    ``existing`` is the ledger's current records — used only to make this idempotent: a
    candidate whose ``scope`` already exists among prior ``source="elicitation_wizard"``
    records is dropped before it is returned. Returns only the newly proposed records (the
    caller appends them to the ledger); ``existing`` itself is never mutated or re-returned.
    """
    live_tables = _live_tables(tables)
    existing_scopes = {r.scope for r in existing if r.source == ELICITATION_SOURCE}

    candidates: list[ClarificationRecord] = []
    candidates += _propose_a(live_tables, assets_by_id, limit_per_category)
    candidates += _propose_c(live_tables, assets_by_id, limit_per_category)
    candidates += _propose_e(live_tables, assets_by_id, limit_per_category)
    candidates += _propose_b(live_tables, assets_by_id, limit_per_category)
    return [c for c in candidates if c.scope not in existing_scopes]


def _propose_a(tables: Sequence[Any], assets_by_id: dict[str, Any], limit: int) -> list[ClarificationRecord]:
    """A: for each ambiguous term found in >=1 column name, a column-picker question over every
    matching ``table.column`` candidate."""
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
    scope = "elicitation:rule:fiscal_year_start"
    return [
        ClarificationRecord(
            id=_record_id(scope),
            scope=scope,
            question="What month does your fiscal year start? (enter 1-12, 1 = January)",
            category="C",
            ui_modality="numeric",
            choices=tuple(
                {"id": str(i), "label": f"{i} - {name}"}
                for i, name in enumerate(_FISCAL_MONTH_NAMES, start=1)
            ),
            allow_freeform=True,
            raised_by=("elicitation_wizard",),
            source=ELICITATION_SOURCE,
        )
    ][:limit]


def _propose_e(tables: Sequence[Any], assets_by_id: dict[str, Any], limit: int) -> list[ClarificationRecord]:
    """E: for a status/rating-like column whose sample values include a null-like sentinel, ask
    whether to exclude it by default."""
    out: list[ClarificationRecord] = []
    for table in tables:
        for column in _columns_of(table, assets_by_id):
            name_lower = column.physical_name.lower()
            if not any(hint in name_lower for hint in _STATUS_HINTS):
                continue
            sentinel = next(
                (
                    str(v)
                    for v in column.sample_values
                    if str(v).strip().lower() in _SENTINEL_VALUES
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


def _propose_b(tables: Sequence[Any], assets_by_id: dict[str, Any], limit: int) -> list[ClarificationRecord]:
    """B: for a small-cardinality categorical column, a checklist of the actual distinct values
    seen (``ColumnAsset.sample_values`` — no live DB query needed for this MVP)."""
    out: list[ClarificationRecord] = []
    for table in tables:
        for column in _columns_of(table, assets_by_id):
            name_lower = column.physical_name.lower()
            if not any(hint in name_lower for hint in _CATEGORICAL_HINTS):
                continue
            values = sorted({str(v) for v in column.sample_values if str(v).strip()})
            if not (1 < len(values) <= 15):
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
        choices=None,
        allow_freeform=True,
        target_table=picked_table,
        target_column=picked_column,
        raised_by=("elicitation_wizard:auto",),
        source=ELICITATION_SOURCE,
    )
