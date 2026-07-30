"""Corpus CI: reference integrity + ID conventions.

A green run is the curator's machine-checkable "done-enough" signal (D9). This
module checks everything verifiable *from the corpus alone*:

- ID regex per asset type (``ids.py``).
- No duplicate ids.
- Reference resolution: ``column.references``, ``term.binding.asset_id``,
  ``term.related_terms[].id``, ``metric.base_table``, ``note.scope[]``,
  ``join.left_table`` / ``right_table`` all resolve to existing assets.
- Join ``on``-clause columns: parsed with ``sqlglot`` and confirmed to belong to
  one of the join's two tables (corpus-only; catches typo'd/hallucinated columns
  that would otherwise mis-join at serve time).
- Always-note prompt budget, evaluated **per turn scope** rather than over the whole
  asset list, since the numbers it enforces describe one analyst prompt
  (``_check_always_note_budget``).
- Metric ``expression`` parseability: ``sqlglot`` parse of the expression fragment
  (dialect from ``settings.datasource.kind`` when available, else postgres).
  Offline-safe — no connector. Does **not** execute the metric or type-check
  columns against a live catalog.

Enum validity is enforced upstream at parse time (``schemas.parse_asset``), so
by the time assets reach here their enum fields are already valid.

Two checks require inputs beyond the corpus and are therefore *optional* hooks
(and belong to the eval harness, not the schema — P2):

- **Physical existence** — every ``physical_name`` / ``on`` column exists in the
  live catalog. Needs a DB connection (pass ``connector``).
- **Leakage guard** — few-shot ``source_refs`` ⊆ train split. Needs the split
  (pass ``train_refs``). BIRD-eval-specific.

  **This one currently protects nothing, and should not be cited as though it
  does.** Two independent reasons: no call site anywhere passes ``train_refs``, and
  nothing populates ``FewShotAsset.audit.provenance.source_refs`` in the first place
  — ``seed_from_train_sql`` receives a list of SQL strings, not the items they came
  from, so it has no question id to stamp. The check would therefore find an empty
  ref set and pass, on every asset, even if it were wired up.

  What actually keeps few-shots out of the test split today is structural, not this:
  ``build_curated_corpus`` is only ever called with ``train_items`` loaded at
  ``split="train"``, and the pooled driver asserts train/test id disjointness before
  serving (``run_datalake._assert_train_test_disjoint``). That is sound but it is
  enforcement by call site, so an edit that widened the seeder's input would sail
  through. Making this guard real means threading question ids into
  ``seed_from_train_sql`` so it can stamp ``source_refs``, then passing
  ``train_refs`` from the one call site that has the split in scope
  (``_validate_fix_pass``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import ids

if TYPE_CHECKING:
    from ..config import Settings
    from ..gateway.connectors.base import Connector

from .schemas import (
    Asset,
    FewShotAsset,
    JoinAsset,
    MetricAsset,
    NoteAsset,
    TableAsset,
    TermAsset,
)

#: The always-injected note budget, named so a producer can respect the same numbers
#: this checker enforces. They were literals here and nowhere else, so
#: ``AssetBag.record_caveats`` could write past the budget and only find out when
#: ``gate_hard_findings`` refused the corpus — which discards the whole build rather
#: than the note that did not fit. Mirrors ``[notes] always_note_global_max`` /
#: ``always_note_char_max`` in Settings; keep them in step.
#:
#: Both are **per-turn** numbers: they describe one analyst prompt, because that is
#: what ``analyst.note_inject.apply_always_budget`` spends them on. Check them per
#: turn scope, never over the whole asset list — see
#: :func:`_check_always_note_budget`.
ALWAYS_NOTE_GLOBAL_MAX = 8
ALWAYS_NOTE_TOTAL_CHARS_MAX = 2000

#: Group key for always-notes that every turn pays for, used when a corpus has no
#: schema-bearing group to fold them into.
_EVERY_TURN = ""


@dataclass(frozen=True)
class Finding:
    """A single CI problem. ``asset_id`` is the offending asset (or "")."""

    code: str
    asset_id: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        where = f" [{self.asset_id}]" if self.asset_id else ""
        return f"{self.code}{where}: {self.message}"


def _column_ids(assets: Iterable[Asset]) -> set[str]:
    out: set[str] = set()
    for a in assets:
        if isinstance(a, TableAsset):
            for col in a.columns:
                out.add(ids.derive_column_id(a.id, col.physical_name))
    return out


def validate_corpus(
    assets: list[Asset],
    *,
    connector: "Connector | None" = None,
    train_refs: set[str] | None = None,
    settings: "Settings | None" = None,
) -> list[Finding]:
    """Validate a parsed corpus. Returns findings; empty list == CI green.

    ``connector`` and ``train_refs`` are optional; when omitted the corresponding
    checks (physical existence, leakage guard) are skipped rather than failing.
    Metric expression parse always runs (offline; dialect from ``settings`` when
    given, else postgres).
    """
    findings: list[Finding] = []
    dialect = _sql_dialect(settings)

    # -- ID regex + duplicate detection ------------------------------------- #
    seen: set[str] = set()
    for a in assets:
        if not ids.is_valid_id(a.asset_type, a.id):
            findings.append(
                Finding("bad-id", a.id, f"id does not match the {a.asset_type} convention")
            )
        if a.id in seen:
            findings.append(Finding("duplicate-id", a.id, "id used by more than one asset"))
        seen.add(a.id)

    # -- (schema, physical_name) uniqueness --------------------------------- #
    # The schema-qualified allowlist / L4 scope key on ``{schema}.{physical_name}``
    # (D15). Two tables sharing a (schema, physical_name) make that qualified key
    # ambiguous - the guardrail could not tell which table a column belongs to.
    # Same-named tables in DIFFERENT schemas are fine (that is the whole point of
    # multi-schema); only a collision within one schema is rejected. For a
    # single-schema corpus this reduces to "physical_name is unique" and is a
    # no-op for a well-formed one.
    by_physical: dict[tuple[str, str], list[str]] = {}
    for a in assets:
        if isinstance(a, TableAsset):
            by_physical.setdefault((a.schema, a.physical_name), []).append(a.id)
    for (schema, physical_name), owners in by_physical.items():
        if len(owners) > 1:
            for owner in owners:
                findings.append(
                    Finding(
                        "ambiguous-physical-table",
                        owner,
                        f"(schema={schema!r}, physical_name={physical_name!r}) is shared by "
                        f"{sorted(owners)}; the schema-qualified allowlist key is ambiguous",
                    )
                )

    # -- Build resolvable id sets ------------------------------------------- #
    table_ids = {a.id for a in assets if isinstance(a, TableAsset)}
    metric_ids = {a.id for a in assets if isinstance(a, MetricAsset)}
    term_ids = {a.id for a in assets if isinstance(a, TermAsset)}
    col_ids = _column_ids(assets)
    all_ids = {a.id for a in assets} | col_ids
    schemas = {a.schema for a in assets if isinstance(a, TableAsset)}
    db_name = settings.datasource.db if settings is not None else "main"

    def require(ref: str | None, pool: set[str], owner: str, what: str) -> None:
        if ref is None:
            return
        if ref.startswith("schema:"):
            if ref.removeprefix("schema:") not in schemas:
                findings.append(
                    Finding("dangling-ref", owner, f"{what} -> '{ref}' does not resolve")
                )
            return
        if ref.startswith("db:"):
            if ref.removeprefix("db:") != db_name:
                findings.append(
                    Finding("dangling-ref", owner, f"{what} -> '{ref}' does not resolve")
                )
            return
        if ref not in pool:
            findings.append(
                Finding("dangling-ref", owner, f"{what} -> '{ref}' does not resolve")
            )

    # -- Reference resolution ----------------------------------------------- #
    for a in assets:
        if isinstance(a, TableAsset):
            for col in a.columns:
                require(col.references, col_ids, a.id, f"column '{col.physical_name}'.references")
        elif isinstance(a, JoinAsset):
            require(a.left_table, table_ids, a.id, "join.left_table")
            require(a.right_table, table_ids, a.id, "join.right_table")
        elif isinstance(a, TermAsset):
            if a.binding is not None:
                pool = {"metric": metric_ids, "table": table_ids, "column": col_ids}[
                    a.binding.asset_type
                ]
                require(a.binding.asset_id, pool, a.id, "term.binding.asset_id")
            for rel in a.related_terms:
                require(rel.id, term_ids, a.id, "term.related_terms[].id")
        elif isinstance(a, MetricAsset):
            require(a.base_table, table_ids, a.id, "metric.base_table")
        elif isinstance(a, NoteAsset):
            for scoped in a.scope:
                require(scoped, all_ids, a.id, "note.scope[]")
            if a.audit is not None:
                published = getattr(a.publication_status, "value", a.publication_status)
                audited = getattr(a.audit.provenance.status, "value", a.audit.provenance.status)
                if published != audited:
                    findings.append(
                        Finding(
                            "publication-status-drift",
                            a.id,
                            f"publication_status={published!r} differs from "
                            f"audit.provenance.status={audited!r}",
                        )
                    )
        elif isinstance(a, FewShotAsset):
            if train_refs is not None:
                prov = a.audit.provenance if a.audit else None
                refs = set(prov.source_refs) if prov else set()
                leaked = refs - train_refs
                if leaked:
                    findings.append(
                        Finding(
                            "leakage",
                            a.id,
                            f"few_shot source_refs not in train split: {sorted(leaked)}",
                        )
                    )

    # -- Always-injected note budget (per turn scope, not per corpus) -------- #
    _check_always_note_budget(assets, findings, db_name=db_name)

    # -- C5: notes must not name governance-excluded identifiers (summary first) -- #
    excluded_tokens = _excluded_identifier_tokens(assets)
    if excluded_tokens:
        for a in assets:
            if not isinstance(a, NoteAsset):
                continue
            for field_name, text in (("summary", a.summary), ("body", a.body or "")):
                if not text:
                    continue
                hits = sorted(
                    tok for tok in excluded_tokens if _names_identifier(text, tok)
                )
                if hits:
                    findings.append(
                        Finding(
                            "note-excluded-identifier",
                            a.id,
                            f"note.{field_name} names excluded identifier(s): {hits}",
                        )
                    )
                    break  # summary first; one finding per note is enough

    # -- Join on-clause columns resolve to the joined tables (corpus-only) --- #
    # Endpoint ids are checked above; the ``on`` SQL is not. A typo'd or
    # hallucinated column in ``on`` otherwise passes CI green and only surfaces
    # (or silently mis-joins) at serve time. Parse it here -- no live catalog
    # needed -- and confirm each referenced column belongs to one of the two
    # joined tables.
    _check_join_on_columns(assets, findings)

    # -- Metric expressions parse as SQL fragments (corpus-only; offline) ---- #
    # Correctness against live data is out of scope for CI; this only rejects
    # expressions sqlglot cannot parse under the datasource dialect.
    _check_metric_expressions(assets, findings, dialect=dialect)

    # -- Physical existence (optional; needs a live catalog) ---------------- #
    # Hook: pass ``connector`` (e.g. from the CLI once a catalog dial is wired)
    # to verify physical_name / columns against the live catalog. Skipped when
    # omitted so offline CI stays green without a DB.
    if connector is not None:
        _check_physical_existence(assets, connector, findings)

    return findings


def _sql_dialect(settings: "Settings | None") -> str:
    """sqlglot dialect for corpus-only SQL fragment checks.

    Prefer the configured datasource kind (``sqlite`` / ``postgres`` /
    ``redshift``); default to postgres when settings are absent (offline CLI /
    unit tests with no config loaded).
    """
    if settings is not None and settings.datasource.kind:
        return settings.datasource.kind.lower()
    return "postgres"


def _excluded_identifier_tokens(assets: list[Asset]) -> set[str]:
    """Physical names of excluded tables/columns (C5 content-scan fodder)."""
    tokens: set[str] = set()
    for a in assets:
        if isinstance(a, TableAsset):
            if a.governance.excluded and a.physical_name:
                tokens.add(a.physical_name)
            for col in a.columns:
                if col.governance.excluded and col.physical_name:
                    tokens.add(col.physical_name)
    return {t for t in tokens if len(t) >= 3}  # skip tiny tokens that false-positive


def _names_identifier(text: str, tok: str) -> bool:
    """Whole-identifier match, not raw substring: excluded `age` must not flag the
    word "average". ``\\b`` treats ``_`` as a word char, so snake_case physical
    names still match as whole tokens. Case-insensitive (Postgres folds unquoted
    identifiers to lowercase)."""
    return re.search(rf"\b{re.escape(tok)}\b", text, re.IGNORECASE) is not None


def _is_always(note: NoteAsset) -> bool:
    """Mirror of ``note_inject._act_value``: a note with no explicit activation is an
    always-note. ``NoteAsset._defaults_from_kind`` fills the field during validation,
    so the ``None`` arm only matters for a note whose field was cleared afterwards."""
    act = note.activation
    if act is None:
        return True
    return getattr(act, "value", act) == "always"


def _scope_schemas(assets: list[Asset]) -> dict[str, set[str]]:
    """Map each asset id usable in ``note.scope`` to the schema(s) whose turns can
    license it.

    A table and its derived column ids carry the table's own schema; a metric inherits
    its base table's; a few-shot carries its own. Ids that name no schema at all
    (terms, notes, negative examples) are absent, and so is a join whose two sides live
    in different schemas — that join is only licensed on a cross-schema turn, which
    :func:`_check_always_note_budget` deliberately does not bound.
    """
    out: dict[str, set[str]] = {}
    tables = {a.id: a for a in assets if isinstance(a, TableAsset)}
    for t in tables.values():
        out[t.id] = {t.schema}
        for col in t.columns:
            out[ids.derive_column_id(t.id, col.physical_name)] = {t.schema}
    for a in assets:
        if isinstance(a, FewShotAsset):
            out[a.id] = {a.schema}
        elif isinstance(a, MetricAsset):
            base = tables.get(a.base_table)
            if base is not None:
                out[a.id] = {base.schema}
        elif isinstance(a, JoinAsset):
            sides = {
                tables[t].schema for t in (a.left_table, a.right_table) if t in tables
            }
            if len(sides) == 1:
                out[a.id] = sides
    return out


def _check_always_note_budget(
    assets: list[Asset], findings: list[Finding], *, db_name: str
) -> None:
    """Enforce the always-note budget per TURN SCOPE rather than per asset list.

    ``ALWAYS_NOTE_GLOBAL_MAX`` and ``ALWAYS_NOTE_TOTAL_CHARS_MAX`` describe one
    analyst prompt: ``note_inject.apply_always_budget`` admits always-notes in
    precedence order until it hits either cap and silently drops the rest. The
    population those numbers bound is therefore "the always-notes a single turn can
    carry", so the check has to group notes the way a turn licenses them.

    Summing over whatever list the caller passes gets that wrong as soon as the caller
    passes more than one schema, and one caller does: ``eval.harness._validate_corpora``
    hands this function an arm's entire pooled corpus, which for the data-lake driver is
    57 schemas at once. On 2026-07-30 that reported ``always-note summaries total 5178
    characters; maximum is 2000`` against a corpus whose worst single schema held 1591
    characters in 4 notes — every schema inside both caps, and the build log recording
    ``0 dropped over the always-note budget``. The finding was false, and it cost a
    1351-question run its quotable status.

    Grouping, mirroring ``note_inject.scope_matches``:

    - A note with an empty scope, or a ``db:`` scope naming this corpus's database,
      matches every turn. It is counted into every group, because every turn pays for
      it.
    - ``schema:<name>`` groups under that schema; an asset id groups under the
      schema(s) that own it (:func:`_scope_schemas`).
    - A scope id that resolves to no schema gets a group of its own rather than a free
      pass. It is already reported as ``dangling-ref`` when it resolves to nothing at
      all.

    Both caps count every always-note in the group. The count cap used to apply only to
    empty-scope notes, which ``apply_always_budget`` established as wrong: in a curated
    corpus nothing is globally scoped, so that reading left the count cap dead and the
    character cap as the only gate, "which let dozens of short caveats in". Validation
    now agrees with what serve does.

    Known limit: a turn licensing tables from two schemas carries the union of both
    groups, and CI cannot bound that — which schemas co-occur is a serve-time routing
    decision, not a corpus property. Widening groups to every pair of joinable schemas
    would repeat the error being fixed here, failing a corpus over a turn no run
    performs. ``apply_always_budget`` covers that case by dropping in precedence order
    instead of failing.
    """
    always_notes = [a for a in assets if isinstance(a, NoteAsset) and _is_always(a)]
    if not always_notes:
        return

    schemas_by_scope = _scope_schemas(assets)
    everywhere: list[NoteAsset] = []
    scoped: list[tuple[NoteAsset, set[str]]] = []
    for note in always_notes:
        if not note.scope:
            everywhere.append(note)
            continue
        keys: set[str] = set()
        matches_every_turn = False
        for sid in note.scope:
            if sid.startswith("db:"):
                if sid.removeprefix("db:") == db_name:
                    matches_every_turn = True
                else:
                    keys.add(sid)  # resolves to nothing; already a dangling-ref
                continue
            if sid.startswith("schema:"):
                keys.add(sid)
                continue
            owners = schemas_by_scope.get(sid)
            keys |= {f"schema:{s}" for s in owners} if owners else {sid}
        if matches_every_turn:
            everywhere.append(note)
        else:
            scoped.append((note, keys))

    groups: dict[str, list[NoteAsset]] = {}
    for _note, keys in scoped:
        for key in keys:
            groups.setdefault(key, list(everywhere))
    if not groups:
        groups[_EVERY_TURN] = list(everywhere)
    for note, keys in scoped:
        for key in keys:
            groups[key].append(note)

    for key in sorted(groups):
        notes = groups[key]
        where = "every turn" if key == _EVERY_TURN else f"a turn scoped to '{key}'"
        if len(notes) > ALWAYS_NOTE_GLOBAL_MAX:
            findings.append(
                Finding(
                    "always-note-budget",
                    "",
                    f"{len(notes)} always notes apply to {where}, over the per-turn "
                    f"maximum of {ALWAYS_NOTE_GLOBAL_MAX}",
                )
            )
        chars = sum(len(n.summary) for n in notes)
        if chars > ALWAYS_NOTE_TOTAL_CHARS_MAX:
            findings.append(
                Finding(
                    "always-note-budget",
                    "",
                    f"always-note summaries for {where} total {chars} characters; the "
                    f"per-turn maximum is {ALWAYS_NOTE_TOTAL_CHARS_MAX}",
                )
            )


def _check_join_on_columns(assets: list[Asset], findings: list[Finding]) -> None:
    import sqlglot
    from sqlglot import exp

    tables_by_id = {a.id: a for a in assets if isinstance(a, TableAsset)}
    for a in assets:
        if not isinstance(a, JoinAsset):
            continue
        left = tables_by_id.get(a.left_table)
        right = tables_by_id.get(a.right_table)
        if left is None or right is None:
            continue  # dangling endpoint already reported above
        cols_by_physical = {
            left.physical_name: {c.physical_name for c in left.columns},
            right.physical_name: {c.physical_name for c in right.columns},
        }
        union = set().union(*cols_by_physical.values())
        try:
            tree = sqlglot.parse_one(a.on)
        except Exception:
            findings.append(
                Finding("join-on-unparseable", a.id, f"join.on is not parseable SQL: {a.on!r}")
            )
            continue
        for col in tree.find_all(exp.Column):
            qualifier = col.table  # physical table name, per the schema contract
            name = col.name
            # A recognised qualifier scopes the check to that table; an alias or
            # unknown qualifier falls back to the union (lenient -- we only want
            # to catch columns that exist in NEITHER joined table).
            pool = cols_by_physical.get(qualifier, union)
            if name not in pool:
                where = f"{qualifier}.{name}" if qualifier else name
                findings.append(
                    Finding(
                        "join-on-unresolved",
                        a.id,
                        f"join.on column '{where}' is not a column of "
                        f"{left.physical_name!r} or {right.physical_name!r}",
                    )
                )


def _check_metric_expressions(
    assets: list[Asset], findings: list[Finding], *, dialect: str
) -> None:
    """Reject metric expressions that sqlglot cannot parse (offline, no connector).

    Does not execute against a live DB, resolve columns, or type-check — only
    catches unparseable fragments before they become authoritative prompt guidance.
    """
    import sqlglot

    for a in assets:
        if not isinstance(a, MetricAsset):
            continue
        expr = (a.expression or "").strip()
        if not expr:
            findings.append(
                Finding(
                    "metric-expression-unparseable",
                    a.id,
                    "metric.expression is empty",
                )
            )
            continue
        try:
            sqlglot.parse_one(expr, read=dialect)
        except Exception as err:
            first = str(err).splitlines()[0] if str(err) else type(err).__name__
            findings.append(
                Finding(
                    "metric-expression-unparseable",
                    a.id,
                    f"metric.expression is not parseable SQL ({dialect}): {expr!r} ({first})",
                )
            )


def _check_physical_existence(
    assets: list[Asset], connector: "Connector", findings: list[Finding]
) -> None:
    """Verify each table's ``physical_name`` and its columns exist in the live
    catalog. Join ``on`` columns are not parsed yet (they need SQL parsing);
    that check is deferred to the eval harness.
    """
    live_tables = set(connector.list_tables())
    for a in assets:
        if not isinstance(a, TableAsset):
            continue
        if a.physical_name not in live_tables:
            findings.append(
                Finding("missing-table", a.id, f"physical_name '{a.physical_name}' not in the catalog")
            )
            continue
        live_columns = {c.name for c in connector.describe_table(a.physical_name).columns}
        for col in a.columns:
            if col.physical_name not in live_columns:
                findings.append(
                    Finding(
                        "missing-column",
                        a.id,
                        f"column '{col.physical_name}' not in table '{a.physical_name}'",
                    )
                )


def is_green(findings: list[Finding]) -> bool:
    return not findings
