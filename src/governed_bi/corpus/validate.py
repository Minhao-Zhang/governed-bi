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

Enum validity is enforced upstream at parse time (``schemas.parse_asset``), so
by the time assets reach here their enum fields are already valid.

Two checks require inputs beyond the corpus and are therefore *optional* hooks
(and belong to the eval harness, not the schema — P2):

- **Physical existence** — every ``physical_name`` / ``on`` column exists in the
  live catalog. Needs a DB connection (pass ``connector``).
- **Leakage guard** — few-shot ``source_refs`` ⊆ train split. Needs the split
  (pass ``train_refs``). BIRD-eval-specific.
"""

from __future__ import annotations

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
    """
    findings: list[Finding] = []

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

    # -- Always-injected note budget ---------------------------------------- #
    always_notes = [
        a
        for a in assets
        if isinstance(a, NoteAsset)
        and getattr(a.activation, "value", a.activation) == "always"
    ]
    global_always = [a for a in always_notes if not a.scope]
    if len(global_always) > 8:
        findings.append(
            Finding(
                "always-note-budget",
                "",
                f"{len(global_always)} global always notes exceed the maximum of 8",
            )
        )
    total_summary_chars = sum(len(a.summary) for a in always_notes)
    if total_summary_chars > 2000:
        findings.append(
            Finding(
                "always-note-budget",
                "",
                f"always-note summaries total {total_summary_chars} characters; maximum is 2000",
            )
        )

    # -- C5: notes must not name governance-excluded identifiers (summary first) -- #
    excluded_tokens = _excluded_identifier_tokens(assets)
    if excluded_tokens:
        for a in assets:
            if not isinstance(a, NoteAsset):
                continue
            for field_name, text in (("summary", a.summary), ("body", a.body or "")):
                if not text:
                    continue
                hits = sorted(tok for tok in excluded_tokens if tok in text)
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

    # -- Physical existence (optional; needs a live catalog) ---------------- #
    if connector is not None:
        _check_physical_existence(assets, connector, findings)

    return findings


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
