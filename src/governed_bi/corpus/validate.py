"""Corpus CI: reference integrity + ID conventions.

A green run is the curator's machine-checkable "done-enough" signal (D9). This
module checks everything verifiable *from the corpus alone*:

- ID regex per asset type (``ids.py``).
- No duplicate ids.
- Reference resolution: ``column.references``, ``term.binding.asset_id``,
  ``term.related_terms[].id``, ``metric.base_table``, ``rule.scope[]``,
  ``join.left_table`` / ``right_table`` all resolve to existing assets.

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
    from ..gateway.connectors.base import Connector
from .schemas import (
    Asset,
    FewShotAsset,
    JoinAsset,
    MetricAsset,
    RuleAsset,
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

    # -- Build resolvable id sets ------------------------------------------- #
    table_ids = {a.id for a in assets if isinstance(a, TableAsset)}
    metric_ids = {a.id for a in assets if isinstance(a, MetricAsset)}
    term_ids = {a.id for a in assets if isinstance(a, TermAsset)}
    col_ids = _column_ids(assets)
    all_ids = {a.id for a in assets} | col_ids

    def require(ref: str | None, pool: set[str], owner: str, what: str) -> None:
        if ref is None:
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
        elif isinstance(a, RuleAsset):
            for scoped in a.scope:
                require(scoped, all_ids, a.id, "rule.scope[]")
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

    # -- Physical existence (optional; needs a live catalog) ---------------- #
    if connector is not None:
        _check_physical_existence(assets, connector, findings)

    return findings


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
