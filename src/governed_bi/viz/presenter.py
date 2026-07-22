"""UI-agnostic view models for the read-only audit surface (docs/viz.md).

This module is the **swappable seam**: it turns the domain objects (the full
corpus, an answer) into plain, frozen dataclasses with no rendering logic and no
UI dependency. The HTTP API (``governed_bi.api``) serializes these as JSON and a
separate frontend renders them; swapping frontends touches only the renderer,
never this module. This repo ships no bundled UI.

It reads the **full** corpus (Facts + Inference + Audit, including
``governance.excluded`` assets), not the ``for_analyst()`` view: the point of the
audit surface is to show the tiers, the provenance, and the exclusions that the
Analyst never sees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..corpus import validate_corpus
from ..corpus.ids import derive_column_id
from ..corpus.schemas import (
    FewShotAsset,
    JoinAsset,
    MetricAsset,
    NegativeExampleAsset,
    NoteAsset,
    TableAsset,
    TermAsset,
)

if TYPE_CHECKING:
    from ..corpus import Corpus
    from ..analyst.answer import Answer

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
class CorpusHealth:
    counts: dict[str, int]  # asset_type -> count
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
    kind: str  # asset_type: table | join | metric | term | note | few_shot | negative_example
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
    """Echo of the scope the Analyst applied (UI ``engineScopeMatches``)."""

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
class ColumnRef:
    """A resolved column identity (used for FK in/out targets)."""

    column_id: str
    table_id: str
    physical_name: str


@dataclass(frozen=True)
class ColumnIdentity:
    """The resolved identity of the queried column."""

    id: str
    table_id: str
    table_physical_name: str
    schema: str
    physical_name: str


@dataclass(frozen=True)
class RelatedTermView:
    id: str
    name: str
    synonyms: list[str]
    confidence: float | None
    provenance_status: str | None


@dataclass(frozen=True)
class RelatedRuleView:
    id: str
    kind: str
    statement: str
    confidence: float | None
    provenance_status: str | None


@dataclass(frozen=True)
class RelatedJoinView:
    id: str
    left_table: str
    right_table: str
    other_table_id: str  # the endpoint that is NOT the queried column's table
    on: str
    cardinality: str | None
    confidence: float | None
    low_confidence: bool


@dataclass(frozen=True)
class RelatedMetricView:
    id: str
    name: str
    granularity: str  # always "table" — metrics have no structured physical column


@dataclass(frozen=True)
class ColumnRelatedView:
    """Every semantic-layer item that touches one physical column (handoff §14)."""

    column: ColumnIdentity
    terms: list[RelatedTermView]
    rules: list[RelatedRuleView]
    fk_out: ColumnRef | None
    fk_in: list[ColumnRef]
    joins: list[RelatedJoinView]
    metrics: list[RelatedMetricView]
    column_resolvable: bool


@dataclass(frozen=True)
class AnswerView:
    tier: str  # display-only projection (ReliabilityTier) for a compact badge; NOT canonical
    safety_clearance: bool  # axis 1 (canonical): guardrails + authorization passed
    semantic_assurance: str  # axis 2 (canonical): how well-grounded (drives delivery), not "is it right"
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
    if isinstance(asset, NoteAsset):
        return f"[{asset.kind.value}] {asset.summary}"
    if isinstance(asset, FewShotAsset):
        return asset.question
    if isinstance(asset, NegativeExampleAsset):
        return asset.pattern
    return asset.asset_type


def asset_rows(corpus: "Corpus", *, asset_types: set[str] | None = None) -> list[AssetRow]:
    """One-line rows for non-table assets (joins, metrics, terms, notes,
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
    if isinstance(asset, NoteAsset):
        return asset.summary
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
        elif isinstance(asset, NoteAsset):
            for scope_id in asset.scope:
                add_edge(asset.id, scope_id, "scopes")
        elif isinstance(asset, FewShotAsset):
            for term_id in asset.bound_terms:
                add_edge(asset.id, term_id, "exemplifies")

    return KnowledgeGraphView(nodes=nodes, edges=edges)


def _parse_join_columns(on: str) -> list[tuple[str, str]]:
    """Parse a physical ON predicate into ``(physical_table, physical_column)`` pairs.

    ``JoinAsset.on`` is a raw physical equality string, e.g.
    ``"transaction.CustomerID = customers.CustomerID"`` — physical names, not asset
    ids (handoff §14.3). Handles composite predicates joined by ``AND`` and strips
    identifier quoting (``"t"."c"``, `` `t`.`c` ``, ``[t].[c]``). Best-effort: a
    clause it cannot split into ``table.column`` is skipped, so an unparseable join
    simply does not match rather than erroring.
    """
    pairs: list[tuple[str, str]] = []
    for clause in re.split(r"\s+and\s+", on, flags=re.IGNORECASE):
        for side in clause.split("="):
            token = re.sub(r'[`"\[\]()]', "", side).strip()
            if "." not in token:
                continue
            tbl, _, col = token.rpartition(".")
            tbl = tbl.split(".")[-1]  # drop a schema qualifier if present
            if tbl and col:
                pairs.append((tbl, col))
    return pairs


def related_to_column(corpus: "Corpus", column_id: str) -> ColumnRelatedView | None:
    """Every semantic-layer item bound to one physical column (handoff §14).

    Returns ``None`` when ``column_id`` does not resolve to a known column (the API
    turns that into a 404). ``column_id`` is the derived id
    ``col_<table>_<physical_name>`` (:func:`corpus.ids.derive_column_id`), the same
    id used by ``Column.references``, ``TermBinding.asset_id``, and ``NoteAsset.scope``.

    Reads the full corpus (like the other audit-surface views), so items on excluded
    assets still show. Joins are resolved server-side from the physical ON predicate
    against each ``JoinAsset``'s ``left_table`` / ``right_table`` (which are asset
    ids), never by string-matching a col id against ``on`` — see §14.3.
    """
    tables = corpus.tables()
    table_by_id = {t.id: t for t in tables}

    # Forward index: derived col id -> (owning table, column). Built once; every
    # col-id lookup (the query target, references targets) resolves against it.
    col_index: dict[str, tuple[TableAsset, object]] = {
        derive_column_id(t.id, c.physical_name): (t, c) for t in tables for c in t.columns
    }

    found = col_index.get(column_id)
    if found is None:
        return None
    table, col = found

    identity = ColumnIdentity(
        id=column_id,
        table_id=table.id,
        table_physical_name=table.physical_name,
        schema=table.schema,
        physical_name=col.physical_name,
    )

    def _ref(target_col_id: str) -> ColumnRef | None:
        hit = col_index.get(target_col_id)
        if hit is None:
            return None
        t, c = hit
        return ColumnRef(column_id=target_col_id, table_id=t.id, physical_name=c.physical_name)

    # FK out: this column's own reference (a col id) resolved to an identity.
    fk_out = _ref(col.references) if col.references else None

    # FK in: any column elsewhere that references this one.
    fk_in: list[ColumnRef] = []
    for t in tables:
        for c in t.columns:
            if c.references == column_id:
                ref = _ref(derive_column_id(t.id, c.physical_name))
                if ref is not None:
                    fk_in.append(ref)
    fk_in.sort(key=lambda r: r.column_id)

    terms: list[RelatedTermView] = []
    rules: list[RelatedRuleView] = []
    metrics: list[RelatedMetricView] = []
    joins: list[RelatedJoinView] = []

    for asset in corpus.assets:
        if isinstance(asset, TermAsset):
            b = asset.binding
            if b is not None and b.asset_type == "column" and b.asset_id == column_id:
                terms.append(
                    RelatedTermView(
                        id=asset.id,
                        name=asset.name,
                        synonyms=list(asset.synonyms),
                        confidence=asset.confidence,
                        provenance_status=_provenance_status(asset),
                    )
                )
        elif isinstance(asset, NoteAsset):
            if column_id in asset.scope:
                rules.append(
                    RelatedRuleView(
                        id=asset.id,
                        kind=asset.kind.value,
                        statement=asset.summary,
                        confidence=asset.confidence,
                        provenance_status=_provenance_status(asset),
                    )
                )
        elif isinstance(asset, MetricAsset):
            if asset.base_table == table.id:
                metrics.append(
                    RelatedMetricView(id=asset.id, name=asset.name, granularity="table")
                )
        elif isinstance(asset, JoinAsset):
            # Map each physical (table, column) in the ON predicate back to a col id
            # via the join's endpoint tables (asset ids), then test against the target.
            endpoints = {
                table_by_id[tid].physical_name: tid
                for tid in (asset.left_table, asset.right_table)
                if tid in table_by_id
            }
            touches = any(
                endpoints.get(phys_tbl) == table.id and phys_col == col.physical_name
                for phys_tbl, phys_col in _parse_join_columns(asset.on)
            )
            if touches:
                other = asset.right_table if asset.left_table == table.id else asset.left_table
                joins.append(
                    RelatedJoinView(
                        id=asset.id,
                        left_table=asset.left_table,
                        right_table=asset.right_table,
                        other_table_id=other,
                        on=asset.on,
                        cardinality=asset.cardinality.value if asset.cardinality else None,
                        confidence=asset.confidence,
                        low_confidence=asset.confidence is not None
                        and asset.confidence <= LOW_CONFIDENCE_JOIN,
                    )
                )

    terms.sort(key=lambda t: t.id)
    rules.sort(key=lambda r: r.id)
    metrics.sort(key=lambda m: m.id)
    joins.sort(key=lambda j: j.id)

    return ColumnRelatedView(
        column=identity,
        terms=terms,
        rules=rules,
        fk_out=fk_out,
        fk_in=fk_in,
        joins=joins,
        metrics=metrics,
        column_resolvable=True,
    )


def _redact_provenance_for_client(provenance: dict) -> dict:
    """Drop bulk result rows from the governance ledger before it leaves the API.

    The middleware ledger snapshots full executed rows (up to ``max_rows``) so
    ``finalize`` can reuse them without a second round-trip; that internal record
    must not be shipped verbatim to the client. The ``Answer`` already carries a
    bounded ``result`` preview, so here the ledger keeps ``columns`` /
    ``row_count`` / ``truncated`` for audit but drops the row bodies (D7: never
    return result rows to the client beyond the bounded preview)."""
    out = dict(provenance)
    ledger = out.get("governance_ledger")
    if isinstance(ledger, list):
        redacted: list = []
        for entry in ledger:
            if isinstance(entry, dict) and isinstance(entry.get("result"), dict):
                res = dict(entry["result"])
                if res.get("rows"):
                    res["rows"] = []
                    res["rows_redacted"] = True
                entry = {**entry, "result": res}
            redacted.append(entry)
        out["governance_ledger"] = redacted
    return out


def answer_view(answer: "Answer") -> AnswerView:
    """Map an Analyst ``Answer`` to display fields: the two canonical stamp axes,
    plus the display-only ``tier`` projection for a compact badge, + trace.

    Surfacing both axes (not just the display-only tier) is deliberate - safety
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
        provenance=_redact_provenance_for_client(answer.provenance),
        result=result,
    )
