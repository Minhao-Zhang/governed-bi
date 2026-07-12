"""UI-agnostic view models for the read-only audit surface (docs/viz.md).

This module is the **swappable seam**: it turns the domain objects (the full
corpus, an answer) into plain, frozen dataclasses with no rendering logic and no
UI dependency. The HTTP API (``governed_bi.api``) serializes these as JSON and a
separate frontend renders them; swapping frontends touches only the renderer,
never this module. This repo ships no bundled UI.

It reads the **full** corpus (Facts + Inference + Audit, including
``governance.excluded`` assets), not the ``for_server()`` view: the point of the
audit surface is to show the tiers, the provenance, and the exclusions that the
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
    # Facts (read-only in the audit view)
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
    schema: str  # D15 namespace (corpus ``TableAsset.schema``)
    row_count: int | None
    description: str | None
    grain: str | None
    confidence: float | None
    excluded: bool
    excluded_reason: str | None
    provenance_status: str | None
    columns: list[ColumnView]


@dataclass(frozen=True)
class ColumnSummary:
    """A lean column row for the catalog list (heavy fields dropped)."""

    physical_name: str
    physical_type: str
    role: str | None
    reliability: str  # "ok" | "suspect"
    excluded: bool


@dataclass(frozen=True)
class TableSummary:
    """A lean table row for the virtualized catalog + client search index.

    Heavy fields (``sample_values``, ``evidence``, ``description``) are dropped;
    full detail is fetched lazily via :func:`table_view_by_id`.
    """

    id: str
    physical_name: str
    schema: str  # D15 namespace (corpus ``TableAsset.schema``)
    row_count: int | None
    n_columns: int
    excluded: bool
    has_suspect: bool  # any column flagged suspect
    provenance_status: str | None
    columns: list[ColumnSummary]


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
    schema: str  # D15 namespace (skill ``SkillFrontmatter.schema``)
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
class ResultTableView:
    """The executed result grid, ready to render as a table."""

    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool


@dataclass(frozen=True)
class SchemaGraphNode:
    """A table in the relationship graph (ER-diagram node)."""

    id: str  # table asset id
    physical_name: str
    row_count: int | None
    n_columns: int
    excluded: bool
    has_suspect: bool  # any column flagged suspect
    schema: str | None = None  # D15 namespace (corpus ``TableAsset.schema``)


@dataclass(frozen=True)
class SchemaGraphEdge:
    """A join/FK relationship between two tables (ER-diagram edge)."""

    id: str  # join asset id
    source: str  # left table asset id
    target: str  # right table asset id
    on: str  # physical equality, e.g. "transaction.CustomerID = customers.CustomerID"
    cardinality: str | None
    confidence: float | None
    low_confidence: bool  # confidence at or below LOW_CONFIDENCE_JOIN


@dataclass(frozen=True)
class SchemaGraphView:
    """The table-relationship graph for the ER visualization (nodes + edges)."""

    nodes: list[SchemaGraphNode]
    edges: list[SchemaGraphEdge]
    boundary: list["BoundaryEdge"] = field(default_factory=list)
    meta: "GraphMeta | None" = None


@dataclass(frozen=True)
class KnowledgeGraphNode:
    """A corpus asset as a node in the full knowledge graph."""

    id: str
    kind: str  # asset_type: table | join | metric | term | rule | few_shot | negative_example
    label: str
    excluded: bool
    provenance_status: str | None
    confidence: float | None = None
    has_suspect: bool = False  # tables only: any column flagged suspect
    schema: str | None = None  # tables only: D15 namespace (corpus ``TableAsset.schema``)


@dataclass(frozen=True)
class KnowledgeGraphEdge:
    """A typed relationship between two corpus assets (ids into the node set)."""

    id: str
    source: str
    target: str
    relation: str  # join | measures | grounds | related:<rel> | scopes | exemplifies
    confidence: float | None = None
    low_confidence: bool = False


@dataclass(frozen=True)
class KnowledgeGraphView:
    """The full corpus knowledge graph (all asset types + their relationships)."""

    nodes: list[KnowledgeGraphNode]
    edges: list[KnowledgeGraphEdge]
    boundary: list["BoundaryEdge"] = field(default_factory=list)
    meta: "GraphMeta | None" = None


@dataclass(frozen=True)
class GraphScopeApplied:
    """Echo of the scope the server applied (UI ``engineScopeMatches``)."""

    schema: str | None = None
    focus: str | None = None
    radius: int | None = None
    node_budget: int | None = None


@dataclass(frozen=True)
class GraphMeta:
    """Envelope metadata for a scoped/bounded graph response."""

    total_nodes: int
    returned_nodes: int
    total_edges: int
    truncated: bool = False
    scope: GraphScopeApplied | None = None


@dataclass(frozen=True)
class BoundaryEdge:
    """A curated cross-schema join with one endpoint outside the current scope."""

    id: str
    in_scope_table: str
    other_schema: str
    other_table_id: str
    other_label: str
    on: str
    cardinality: str | None = None
    confidence: float | None = None
    low_confidence: bool = False


@dataclass(frozen=True)
class AnswerView:
    tier: str  # the collapsed single-axis stamp (kept for a compact badge)
    safety_clearance: bool  # axis 1: guardrails + authorization passed
    semantic_assurance: str  # axis 2: how well-grounded (drives delivery), not "is it right"
    text: str | None
    sql: str | None
    escalation: str | None
    provenance: dict
    result: ResultTableView | None = None  # the executed rows (None on refusal)


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


def _table_view(table: TableAsset) -> TableView:
    return TableView(
        id=table.id,
        physical_name=table.physical_name,
        schema=table.schema,
        row_count=table.row_count,
        description=table.description,
        grain=table.grain,
        confidence=table.confidence,
        excluded=table.governance.excluded,
        excluded_reason=table.governance.reason,
        provenance_status=_provenance_status(table),
        columns=[_column_view(table, c) for c in table.columns],
    )


def table_views(corpus: "Corpus") -> list[TableView]:
    """The table view (Facts + Inference side by side), one per table asset.

    Ordered by asset id so paginated ``/schema`` reads stay stable across
    processes: corpus load order is otherwise filesystem-dependent for a
    multi-namespace corpus, so ``offset``/``limit`` could skip or repeat rows
    between workers or a restart."""
    return [_table_view(table) for table in sorted(corpus.tables(), key=lambda t: t.id)]


def table_view_by_id(corpus: "Corpus", table_id: str) -> TableView | None:
    """The full detail view for one table by asset id, or ``None`` if unknown.

    Reuses the same projection as :func:`table_views`; the caller (the API) turns
    a ``None`` into a 404."""
    for table in corpus.tables():
        if table.id == table_id:
            return _table_view(table)
    return None


def table_summaries(corpus: "Corpus", schema: str | None = None) -> list[TableSummary]:
    """Lean catalog rows, one per table asset, optionally filtered to one schema.

    Drops the heavy per-column/per-table fields (sample values, evidence,
    descriptions) so the catalog list + client search index stay small; full
    detail is fetched lazily via :func:`table_view_by_id`. Reads the full corpus,
    so ``excluded`` tables are still listed (flagged) for the audit view."""
    summaries: list[TableSummary] = []
    # id-ordered so offset/limit pagination is stable across processes (see table_views).
    for table in sorted(corpus.tables(), key=lambda t: t.id):
        if schema is not None and table.schema != schema:
            continue
        summaries.append(
            TableSummary(
                id=table.id,
                physical_name=table.physical_name,
                schema=table.schema,
                row_count=table.row_count,
                n_columns=len(table.columns),
                excluded=table.governance.excluded,
                has_suspect=any(c.reliability.status.value == "suspect" for c in table.columns),
                provenance_status=_provenance_status(table),
                columns=[
                    ColumnSummary(
                        physical_name=c.physical_name,
                        physical_type=c.physical_type,
                        role=c.role.value if c.role else None,
                        reliability=c.reliability.status.value,
                        excluded=c.governance.excluded,
                    )
                    for c in table.columns
                ],
            )
        )
    return summaries


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
            schema=skill.frontmatter.schema,
            body=skill.body,
        )
        for skill in corpus.skills
    ]


def schema_graph(corpus: "Corpus") -> SchemaGraphView:
    """The table-relationship graph for the ER view: table nodes + join edges.

    Built directly from the corpus assets (``TableAsset`` nodes, ``JoinAsset``
    edges) rather than the planning graph, so edges carry the curator's join
    ``confidence`` and ``cardinality`` — a frontend can render a low-confidence
    join differently. ``source``/``target`` are table-asset ids (equal to the
    node ids). Reads the full corpus, so ``excluded`` tables are still shown
    (flagged) for the audit view.
    """
    nodes = [
        SchemaGraphNode(
            id=table.id,
            physical_name=table.physical_name,
            row_count=table.row_count,
            n_columns=len(table.columns),
            excluded=table.governance.excluded,
            has_suspect=any(c.reliability.status.value == "suspect" for c in table.columns),
            schema=table.schema,
        )
        for table in corpus.tables()
    ]
    edges = [
        SchemaGraphEdge(
            id=asset.id,
            source=asset.left_table,
            target=asset.right_table,
            on=asset.on,
            cardinality=asset.cardinality.value if asset.cardinality else None,
            confidence=asset.confidence,
            low_confidence=asset.confidence is not None and asset.confidence <= LOW_CONFIDENCE_JOIN,
        )
        for asset in corpus.assets
        if isinstance(asset, JoinAsset)
    ]
    return SchemaGraphView(nodes=nodes, edges=edges)


def _kg_label(asset) -> str:
    """A short human label for a knowledge-graph node."""
    if isinstance(asset, TableAsset):
        return asset.physical_name
    if isinstance(asset, (MetricAsset, TermAsset)):
        return asset.name
    if isinstance(asset, JoinAsset):
        return asset.on
    if isinstance(asset, RuleAsset):
        return asset.statement
    if isinstance(asset, FewShotAsset):
        return asset.question
    if isinstance(asset, NegativeExampleAsset):
        return asset.pattern
    return asset.id


def knowledge_graph(corpus: "Corpus") -> KnowledgeGraphView:
    """The full-corpus knowledge graph: every asset a node, typed relationships as
    edges.

    Edges: a join to each of its two tables; a metric to its ``base_table``; a
    term to its ``binding`` and to each related term; a rule to each asset in its
    ``scope``; a few-shot to each of its ``bound_terms``. Columns are not separate
    nodes (they live in :func:`table_views`); a binding or scope that targets a
    column is redirected to the column's owning table so the relationship still
    shows. Reads the full corpus, so excluded assets are still shown (flagged) for
    the audit view. Duplicate edges are collapsed (e.g. a self-join), and an edge
    whose target is not a node is dropped, so the graph is always internally
    consistent; a frontend filters/layers by ``node.kind`` (tables + joins for the
    ER view).
    """
    nodes: list[KnowledgeGraphNode] = []
    node_ids: set[str] = set()
    for asset in corpus.assets:
        governance = getattr(asset, "governance", None)
        has_suspect = (
            any(c.reliability.status.value == "suspect" for c in asset.columns)
            if isinstance(asset, TableAsset)
            else False
        )
        nodes.append(
            KnowledgeGraphNode(
                id=asset.id,
                kind=asset.asset_type,
                label=_kg_label(asset),
                excluded=bool(getattr(governance, "excluded", False)),
                provenance_status=_provenance_status(asset),
                confidence=getattr(asset, "confidence", None),
                has_suspect=has_suspect,
                schema=asset.schema if isinstance(asset, TableAsset) else None,
            )
        )
        node_ids.add(asset.id)

    # Columns are not nodes, but a term binding / rule scope may target one; map a
    # derived column id back to its owning table so such an edge lands on the table
    # node instead of being silently dropped (the relationship still shows).
    col_to_table: dict[str, str] = {
        derive_column_id(table.id, col.physical_name): table.id
        for table in corpus.tables()
        for col in table.columns
    }

    edges: list[KnowledgeGraphEdge] = []
    seen_edge_ids: set[str] = set()

    def add_edge(source, target, relation, *, confidence=None, low_confidence=False):
        target = col_to_table.get(target, target)  # redirect a column target to its table
        if source not in node_ids or target not in node_ids:
            return
        edge_id = f"{source}->{target}:{relation}"
        if edge_id in seen_edge_ids:  # dedup (e.g. a self-join, or a repeated scope id)
            return
        seen_edge_ids.add(edge_id)
        edges.append(
            KnowledgeGraphEdge(
                id=edge_id,
                source=source,
                target=target,
                relation=relation,
                confidence=confidence,
                low_confidence=low_confidence,
            )
        )

    for asset in corpus.assets:
        if isinstance(asset, JoinAsset):
            low = asset.confidence is not None and asset.confidence <= LOW_CONFIDENCE_JOIN
            add_edge(asset.id, asset.left_table, "join", confidence=asset.confidence, low_confidence=low)
            add_edge(asset.id, asset.right_table, "join", confidence=asset.confidence, low_confidence=low)
        elif isinstance(asset, MetricAsset):
            add_edge(asset.id, asset.base_table, "measures", confidence=asset.confidence)
        elif isinstance(asset, TermAsset):
            if asset.binding is not None:
                add_edge(asset.id, asset.binding.asset_id, "grounds", confidence=asset.confidence)
            for related in asset.related_terms:
                add_edge(asset.id, related.id, f"related:{related.relation.value}")
        elif isinstance(asset, RuleAsset):
            for scope_id in asset.scope:
                add_edge(asset.id, scope_id, "scopes")
        elif isinstance(asset, FewShotAsset):
            for term_id in asset.bound_terms:
                add_edge(asset.id, term_id, "exemplifies")

    return KnowledgeGraphView(nodes=nodes, edges=edges)


def answer_view(answer: "Answer") -> AnswerView:
    """Map a server ``Answer`` to display fields: the two stamp axes + trace.

    Surfacing both axes (not just the collapsed tier) is deliberate - safety
    clearance and semantic assurance mean different things, and the audit surface
    should not let a reviewer read one as the other.
    """
    result = None
    if answer.result is not None:
        result = ResultTableView(
            columns=list(answer.result.columns),
            rows=[list(row) for row in answer.result.rows],
            row_count=answer.result.row_count,
            truncated=answer.result.truncated,
        )
    return AnswerView(
        tier=answer.tier.value,
        safety_clearance=answer.safety_clearance,
        semantic_assurance=answer.semantic_assurance.value,
        text=answer.text,
        sql=answer.sql,
        escalation=answer.escalation,
        provenance=dict(answer.provenance),
        result=result,
    )
