"""UI-agnostic view models for the audit cockpit (docs/viz.md).

This module is the **swappable seam**: it turns the domain objects (the full
corpus, an answer) into plain, frozen dataclasses with no rendering logic and no
UI dependency. ``governed_bi.viz.app`` (Streamlit) renders these; a different
frontend (Marimo, a web app, a static export) would reuse this module unchanged
and only rewrite the renderer.

It reads the **full** corpus (Facts + Inference + Audit, including
``governance.excluded`` assets), not the ``for_server()`` view: the whole point
of the cockpit is to show the tiers, the provenance, and the exclusions that the
server never sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..corpus import validate_corpus
from ..corpus.ids import derive_column_id
from ..corpus.schemas import (
    FewShotAsset,
    JoinAsset,
    MetricAsset,
    NegativeExampleAsset,
    RuleAsset,
    TableAsset,
    TermAsset,
)

if TYPE_CHECKING:
    from ..corpus import Corpus
    from ..server.answer import Answer

# A join at or below this confidence is flagged in the health view (tunable).
LOW_CONFIDENCE_JOIN = 0.7


@dataclass(frozen=True)
class ColumnView:
    # Facts (read-only in the cockpit)
    physical_name: str
    physical_type: str
    logical_type: str
    nullable: bool
    is_unique: bool
    sample_values: list = field(default_factory=list)
    # Inference (editable)
    description: str | None = None
    role: str | None = None
    references: str | None = None
    confidence: float | None = None
    # Governance + reliability + audit
    reliability: str = "ok"
    reliability_note: str | None = None
    excluded: bool = False
    excluded_reason: str | None = None
    provenance_status: str | None = None
    evidence: str | None = None


@dataclass(frozen=True)
class TableView:
    id: str
    physical_name: str
    db: str
    row_count: int | None
    description: str | None
    grain: str | None
    confidence: float | None
    excluded: bool
    excluded_reason: str | None
    provenance_status: str | None
    columns: list[ColumnView]


@dataclass(frozen=True)
class AssetRow:
    """A one-line view of a non-table asset for listings."""

    id: str
    asset_type: str
    summary: str
    provenance_status: str | None
    excluded: bool


@dataclass(frozen=True)
class SkillView:
    skill_id: str
    kind: str
    db: str
    body: str


@dataclass(frozen=True)
class CorpusHealth:
    counts: dict[str, int]  # asset_type -> count
    n_skills: int
    n_suspect_columns: int
    n_excluded: int  # excluded tables + columns
    n_low_confidence_joins: int
    ci_green: bool
    findings: list[str]  # validator finding messages (empty when green)


@dataclass(frozen=True)
class AnswerView:
    tier: str  # the collapsed single-axis stamp (kept for a compact badge)
    safety_clearance: bool  # axis 1: guardrails + authorization passed
    semantic_assurance: str  # axis 2: how well-grounded (drives delivery), not "is it right"
    text: str | None
    sql: str | None
    escalation: str | None
    provenance: dict


def _provenance_status(asset) -> str | None:
    audit = getattr(asset, "audit", None)
    return audit.provenance.status.value if audit is not None else None


def _evidence(asset) -> str | None:
    audit = getattr(asset, "audit", None)
    if audit is None:
        return None
    extra = getattr(audit, "model_extra", None) or {}
    value = extra.get("evidence")
    return str(value) if value is not None else None


def corpus_health(corpus: "Corpus") -> CorpusHealth:
    """Summarize corpus health: asset counts, CI status, and the flags a reviewer
    triages first (suspect columns, exclusions, low-confidence joins)."""
    counts: dict[str, int] = {}
    n_suspect = 0
    n_excluded = 0
    n_low_conf_joins = 0
    for asset in corpus.assets:
        counts[asset.asset_type] = counts.get(asset.asset_type, 0) + 1
        if isinstance(asset, TableAsset):
            if asset.governance.excluded:
                n_excluded += 1
            for col in asset.columns:
                if col.reliability.status.value == "suspect":
                    n_suspect += 1
                if col.governance.excluded:
                    n_excluded += 1
        elif isinstance(asset, JoinAsset):
            if asset.confidence is not None and asset.confidence <= LOW_CONFIDENCE_JOIN:
                n_low_conf_joins += 1

    findings = [str(f) for f in validate_corpus(corpus.assets)]
    return CorpusHealth(
        counts=counts,
        n_skills=len(corpus.skills),
        n_suspect_columns=n_suspect,
        n_excluded=n_excluded,
        n_low_confidence_joins=n_low_conf_joins,
        ci_green=not findings,
        findings=findings,
    )


def _column_view(table: TableAsset, col) -> ColumnView:
    return ColumnView(
        physical_name=col.physical_name,
        physical_type=col.physical_type,
        logical_type=col.logical_type.value,
        nullable=col.nullable,
        is_unique=col.is_unique,
        sample_values=list(col.sample_values),
        description=col.description,
        role=col.role.value if col.role else None,
        references=col.references,
        confidence=col.confidence,
        reliability=col.reliability.status.value,
        reliability_note=col.reliability.note,
        excluded=col.governance.excluded,
        excluded_reason=col.governance.reason,
        provenance_status=_provenance_status(col),
        evidence=_evidence(col),
    )


def table_views(corpus: "Corpus") -> list[TableView]:
    """The table view (Facts + Inference side by side), one per table asset."""
    views: list[TableView] = []
    for table in corpus.tables():
        views.append(
            TableView(
                id=table.id,
                physical_name=table.physical_name,
                db=table.db,
                row_count=table.row_count,
                description=table.description,
                grain=table.grain,
                confidence=table.confidence,
                excluded=table.governance.excluded,
                excluded_reason=table.governance.reason,
                provenance_status=_provenance_status(table),
                columns=[_column_view(table, c) for c in table.columns],
            )
        )
    return views


def _summary(asset) -> str:
    if isinstance(asset, JoinAsset):
        return f"{asset.on} ({asset.cardinality.value if asset.cardinality else 'n/a'})"
    if isinstance(asset, MetricAsset):
        return f"{asset.name}: {asset.expression}"
    if isinstance(asset, TermAsset):
        return f"{asset.name} = {', '.join(asset.synonyms) or '(no synonyms)'}"
    if isinstance(asset, RuleAsset):
        return f"[{asset.kind.value}] {asset.statement}"
    if isinstance(asset, FewShotAsset):
        return asset.question
    if isinstance(asset, NegativeExampleAsset):
        return asset.pattern
    return asset.asset_type


def asset_rows(corpus: "Corpus", *, asset_types: set[str] | None = None) -> list[AssetRow]:
    """One-line rows for non-table assets (joins, metrics, terms, rules,
    few-shots, negatives), optionally filtered to ``asset_types``."""
    rows: list[AssetRow] = []
    for asset in corpus.assets:
        if isinstance(asset, TableAsset):
            continue
        if asset_types is not None and asset.asset_type not in asset_types:
            continue
        rows.append(
            AssetRow(
                id=asset.id,
                asset_type=asset.asset_type,
                summary=_summary(asset),
                provenance_status=_provenance_status(asset),
                excluded=getattr(getattr(asset, "governance", None), "excluded", False),
            )
        )
    return rows


def skill_views(corpus: "Corpus") -> list[SkillView]:
    return [
        SkillView(
            skill_id=skill.frontmatter.skill_id,
            kind=skill.frontmatter.kind.value,
            db=skill.frontmatter.db,
            body=skill.body,
        )
        for skill in corpus.skills
    ]


def answer_view(answer: "Answer") -> AnswerView:
    """Map a server ``Answer`` to display fields: the two stamp axes + trace.

    Surfacing both axes (not just the collapsed tier) is deliberate - safety
    clearance and semantic assurance mean different things, and the cockpit should
    not let a reviewer read one as the other.
    """
    return AnswerView(
        tier=answer.tier.value,
        safety_clearance=answer.safety_clearance,
        semantic_assurance=answer.semantic_assurance.value,
        text=answer.text,
        sql=answer.sql,
        escalation=answer.escalation,
        provenance=dict(answer.provenance),
    )
