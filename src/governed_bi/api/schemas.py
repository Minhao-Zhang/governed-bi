"""Pydantic request/response models for the HTTP API.

These mirror the UI-agnostic view models in ``governed_bi.viz.presenter`` (and the
serve ``Answer``) 1:1, so serialization is ``Model.model_validate(view)`` and the
generated OpenAPI schema is the exact contract a typed frontend consumes. Keeping
them here (not reusing the dataclasses directly) gives FastAPI a clean, typed
schema and decouples the wire format from internal types.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _View(BaseModel):
    """Base for response models built from presenter dataclasses via attributes."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# Wire namespace field: Python attr ``namespace`` serializes as ``schema`` so we
# do not shadow ``BaseModel.schema`` (Pydantic warning) while matching the UI.
_NAMESPACE = Field(validation_alias="schema", serialization_alias="schema")


# ── capabilities ──────────────────────────────────────────────────────────── #
class CapabilitiesResponse(_View):
    environment: str  # "dev" | "prod"
    dialect: str  # sqlglot dialect of the connected data source, e.g. "sqlite"
    can_edit: bool  # whether corpus editing is exposed by this backend
    edit_mode: str | None  # "file" | "pr" | null
    model: str | None  # LLM model name when a live model is wired, else null
    has_live_model: bool
    can_stream: bool  # whether a streaming chat endpoint exists (False for this REST API)
    can_scope: bool  # whether the summary/detail/scoping schema routes are served
    can_search: bool  # whether a server-side FTS endpoint exists (False: client Fuse)
    can_clarify: bool = False  # whether serve-time HITL (ask_user interrupts) is available


# ── health ────────────────────────────────────────────────────────────────── #
class HealthResponse(_View):
    counts: dict[str, int]
    n_skills: int
    n_suspect_columns: int
    n_excluded: int
    n_low_confidence_joins: int
    ci_green: bool
    findings: list[str]


# ── schema (tables + columns) ─────────────────────────────────────────────── #
class ColumnResponse(_View):
    physical_name: str
    physical_type: str
    logical_type: str
    nullable: bool
    is_unique: bool
    sample_values: list[Any]
    description: str | None
    role: str | None
    references: str | None
    confidence: float | None
    reliability: str  # "ok" | "suspect"
    reliability_note: str | None
    excluded: bool
    excluded_reason: str | None
    provenance_status: str | None
    evidence: str | None


class TableResponse(_View):
    id: str
    physical_name: str
    namespace: str = _NAMESPACE
    row_count: int | None
    description: str | None
    grain: str | None
    confidence: float | None
    excluded: bool
    excluded_reason: str | None
    provenance_status: str | None
    columns: list[ColumnResponse]


# ── schema summary (lean catalog) ─────────────────────────────────────────── #
class ColumnSummaryResponse(_View):
    physical_name: str
    physical_type: str
    role: str | None
    reliability: str  # "ok" | "suspect"
    excluded: bool


class TableSummaryResponse(_View):
    # Lean catalog row: heavy fields (sample_values, evidence, description) dropped.
    id: str
    physical_name: str
    namespace: str = _NAMESPACE
    row_count: int | None
    n_columns: int
    excluded: bool
    has_suspect: bool
    provenance_status: str | None
    columns: list[ColumnSummaryResponse]


class SchemaSummaryResponse(_View):
    total: int  # count BEFORE pagination
    items: list[TableSummaryResponse]


# ── relationship graph (ER view) ──────────────────────────────────────────── #
class SchemaGraphNodeResponse(_View):
    id: str
    physical_name: str
    row_count: int | None
    n_columns: int
    excluded: bool
    has_suspect: bool
    namespace: str | None = Field(
        default=None, validation_alias="schema", serialization_alias="schema"
    )


class SchemaGraphEdgeResponse(_View):
    id: str
    source: str
    target: str
    on: str
    cardinality: str | None
    confidence: float | None
    low_confidence: bool


class BoundaryEdgeResponse(_View):
    id: str
    in_scope_table: str
    other_schema: str
    other_table_id: str
    other_label: str
    on: str
    cardinality: str | None = None
    confidence: float | None = None
    low_confidence: bool = False


class GraphScopeResponse(_View):
    schema_ns: str | None = Field(
        default=None, validation_alias="schema", serialization_alias="schema"
    )
    focus: str | None = None
    radius: int | None = None
    node_budget: int | None = None


class GraphMetaResponse(_View):
    total_nodes: int
    returned_nodes: int
    total_edges: int
    truncated: bool = False
    scope: GraphScopeResponse | None = None


class SchemaGraphResponse(_View):
    nodes: list[SchemaGraphNodeResponse]
    edges: list[SchemaGraphEdgeResponse]
    boundary: list[BoundaryEdgeResponse] = Field(default_factory=list)
    meta: GraphMetaResponse | None = None


# ── knowledge graph (full corpus) ─────────────────────────────────────────── #
class KnowledgeGraphNodeResponse(_View):
    id: str
    kind: str  # table | join | metric | term | rule | few_shot | negative_example
    label: str
    excluded: bool
    provenance_status: str | None
    confidence: float | None = None
    has_suspect: bool = False
    namespace: str | None = Field(
        default=None, validation_alias="schema", serialization_alias="schema"
    )  # tables only


class KnowledgeGraphEdgeResponse(_View):
    id: str
    source: str
    target: str
    relation: str  # join | measures | grounds | related:<rel> | scopes | exemplifies
    confidence: float | None = None
    low_confidence: bool = False


class KnowledgeGraphResponse(_View):
    nodes: list[KnowledgeGraphNodeResponse]
    edges: list[KnowledgeGraphEdgeResponse]
    boundary: list[BoundaryEdgeResponse] = Field(default_factory=list)
    meta: GraphMetaResponse | None = None


# ── column → related semantic items (handoff §14) ─────────────────────────── #
class ColumnRefResponse(_View):
    column_id: str
    table_id: str
    physical_name: str


class ColumnIdentityResponse(_View):
    id: str
    table_id: str
    table_physical_name: str
    namespace: str = _NAMESPACE
    physical_name: str


class RelatedTermResponse(_View):
    id: str
    name: str
    synonyms: list[str]
    confidence: float | None
    provenance_status: str | None


class RelatedRuleResponse(_View):
    id: str
    kind: str
    statement: str
    confidence: float | None
    provenance_status: str | None


class RelatedJoinResponse(_View):
    id: str
    left_table: str
    right_table: str
    other_table_id: str
    on: str
    cardinality: str | None
    confidence: float | None
    low_confidence: bool


class RelatedMetricResponse(_View):
    id: str
    name: str
    granularity: str  # always "table" — metrics resolve only to their base table


class ColumnRelatedMetaResponse(_View):
    column_resolvable: bool


class ColumnRelatedResponse(_View):
    column: ColumnIdentityResponse
    terms: list[RelatedTermResponse]
    rules: list[RelatedRuleResponse]
    fk_out: ColumnRefResponse | None
    fk_in: list[ColumnRefResponse]
    joins: list[RelatedJoinResponse]
    metrics: list[RelatedMetricResponse]
    meta: ColumnRelatedMetaResponse


# ── corpus assets + skills ────────────────────────────────────────────────── #
# The selectable non-table asset types (tables have their own /schema view). Used
# to constrain the /corpus/assets ?type= filter so unknown values 422 and the
# valid set is published in the OpenAPI schema.
AssetTypeFilter = Literal["join", "metric", "term", "rule", "few_shot", "negative_example"]


class AssetRowResponse(_View):
    id: str
    asset_type: str
    summary: str
    provenance_status: str | None
    excluded: bool


class SkillResponse(_View):
    skill_id: str
    kind: str
    namespace: str = _NAMESPACE
    body: str


# ── chat ──────────────────────────────────────────────────────────────────── #
class TurnIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    # Strip surrounding whitespace so a whitespace-only question fails min_length
    # (min_length alone would let "   " through), and bound sizes so a client
    # can't push a degenerate/oversized payload into the serve flow.
    model_config = ConfigDict(str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=8000)
    session_id: str = Field("default", min_length=1, max_length=128)
    history: list[TurnIn] = Field(default_factory=list, max_length=100)
    identity: str | None = None  # accepted but not enforced near-term (single demo identity)


class ResultTableResponse(_View):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool


class AnswerResponse(_View):
    tier: str  # governed | lineage | fenced_raw | refused
    safety_clearance: bool
    semantic_assurance: str  # certified | heuristic | unverified | none
    text: str | None
    sql: str | None
    escalation: str | None
    provenance: dict[str, Any]
    result: ResultTableResponse | None


# ── corpus edit (dev only; gated on capabilities.can_edit) ────────────────── #
class EditRequest(BaseModel):
    """A corpus asset to validate and write. ``asset`` is the raw asset mapping
    (same shape as the on-disk YAML), discriminated by its ``asset_type``."""

    asset: dict[str, Any]


class EditResponse(BaseModel):
    written: bool  # False when validation blocked the write (see findings)
    asset_id: str
    asset_type: str
    path: str | None  # repo-relative path written (null when not written)
    findings: list[str]  # reference-integrity findings (empty = clean)
    diff: str  # unified diff of the YAML file (old vs new)
