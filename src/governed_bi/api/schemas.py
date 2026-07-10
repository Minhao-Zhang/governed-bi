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

    model_config = ConfigDict(from_attributes=True)


# ── capabilities ──────────────────────────────────────────────────────────── #
class CapabilitiesResponse(_View):
    environment: str  # "dev" | "prod"
    dialect: str  # sqlglot dialect of the connected data source, e.g. "sqlite"
    can_edit: bool  # whether corpus editing is exposed by this backend
    edit_mode: str | None  # "file" | "pr" | null
    model: str | None  # LLM model name when a live model is wired, else null
    has_live_model: bool


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
    db: str
    row_count: int | None
    description: str | None
    grain: str | None
    confidence: float | None
    excluded: bool
    excluded_reason: str | None
    provenance_status: str | None
    columns: list[ColumnResponse]


# ── relationship graph (ER view) ──────────────────────────────────────────── #
class SchemaGraphNodeResponse(_View):
    id: str
    physical_name: str
    row_count: int | None
    n_columns: int
    excluded: bool
    has_suspect: bool


class SchemaGraphEdgeResponse(_View):
    id: str
    source: str
    target: str
    on: str
    cardinality: str | None
    confidence: float | None
    low_confidence: bool


class SchemaGraphResponse(_View):
    nodes: list[SchemaGraphNodeResponse]
    edges: list[SchemaGraphEdgeResponse]


# ── corpus assets + skills ────────────────────────────────────────────────── #
class AssetRowResponse(_View):
    id: str
    asset_type: str
    summary: str
    provenance_status: str | None
    excluded: bool


class SkillResponse(_View):
    skill_id: str
    kind: str
    db: str
    body: str


# ── chat ──────────────────────────────────────────────────────────────────── #
class TurnIn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class ChatRequest(BaseModel):
    question: str
    session_id: str = "default"
    history: list[TurnIn] = Field(default_factory=list)
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
