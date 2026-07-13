"""Corpus asset schemas (Pydantic).

The typed-asset half of the corpus contract (D9). This is the canonical
implementation of the field spec in ``docs/asset-schemas.md``.

Two principles from the spec:

- **P1 — three field tiers.** Every asset splits into **Facts** (read from the
  catalog/data, never inferred), **Inference** (curator writes / gold fills;
  the semantic layer), and **Audit** (why the inference was made). The tiers are
  grouped by comment below; ``Audit`` is a nested block.
- **P2 — universal fields, project-specific values only.** No field name is
  BIRD-specific. BIRD, enterprise deployments, and any future project share the exact same
  schema; only values differ. BIRD-eval rules (e.g. leakage guards) live in the
  eval harness, never here.

Structured tiers use ``extra="forbid"`` so a mistyped field name fails CI. The
``Audit`` and ``Provenance`` blocks use ``extra="allow"`` because evidence prose
and human-appended provenance entries vary.
"""

from __future__ import annotations

import warnings
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

# `schema` is our canonical, domain-accurate field name (D15) on several assets. It
# harmlessly shadows the deprecated ``BaseModel.schema()`` method — nothing calls
# that (JSON schema uses ``model_json_schema()``). Silence only that specific
# pydantic warning rather than rename the field across corpus/API/UI. Scoped to the
# exact message so genuine field-shadow mistakes still surface.
warnings.filterwarnings(
    "ignore",
    message=r'Field name "schema".*shadows an attribute',
    category=UserWarning,
)

# --------------------------------------------------------------------------- #
# Enums (the CI-checked value sets)
# --------------------------------------------------------------------------- #


class ProvenanceSource(str, Enum):
    curator = "curator"
    gold = "gold"
    human = "human"


class ProvenanceStatus(str, Enum):
    proposed = "proposed"  # proposer emitted it
    draft = "draft"  # adversary passed it
    certified = "certified"  # human signed off (prod only, D6)


class ClarificationStatus(str, Enum):
    open = "open"  # curator asked; awaiting a Responder answer (D12)
    answered = "answered"  # a human/SME answer was accepted into the asset


class ColumnRole(str, Enum):
    primary_key = "primary_key"
    foreign_key = "foreign_key"
    key = "key"
    measure = "measure"
    dimension = "dimension"


class ReliabilityStatus(str, Enum):
    ok = "ok"
    suspect = "suspect"  # AI-inferred reliability caveat (curator, not human)


class LogicalType(str, Enum):
    string = "string"
    integer = "integer"
    decimal = "decimal"
    date = "date"
    datetime = "datetime"
    boolean = "boolean"


class Complexity(str, Enum):
    simple = "simple"
    medium = "medium"
    complex = "complex"


class Cardinality(str, Enum):
    one_to_one = "one_to_one"
    one_to_many = "one_to_many"
    many_to_one = "many_to_one"
    many_to_many = "many_to_many"


class TermRelation(str, Enum):
    synonym_of = "synonym_of"
    broader_than = "broader_than"
    uses = "uses"


class RuleKind(str, Enum):
    business_rule = "business_rule"
    context = "context"
    constraint = "constraint"


class SkillKind(str, Enum):
    routing = "routing"
    gotchas = "gotchas"
    pattern = "pattern"
    domain_overview = "domain_overview"


# A confidence score in [0, 1]. Optional on assets that may be unscored.
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


# --------------------------------------------------------------------------- #
# Shared blocks
# --------------------------------------------------------------------------- #


class _Strict(BaseModel):
    """Base for structured tiers: unknown fields are an error (catches typos)."""

    model_config = ConfigDict(extra="forbid")


class Provenance(BaseModel):
    """Who produced/last-touched an asset and its lifecycle status.

    ``extra="allow"`` so a human edit can append fields (``by``, ``at``,
    ``reason``) without a schema change (Viz appends on certify).
    """

    model_config = ConfigDict(extra="allow")

    source: ProvenanceSource
    status: ProvenanceStatus
    model: str | None = None
    version: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    built_at: str | None = None


class Clarification(_Strict):
    """A curator-emitted open question about the asset it hangs on (D12).

    ID-tracked by the asset it is attached to (the asset carries the ``id``). It
    lives on the ``Audit`` tier, which is never injected into the server context,
    so an open question never leaks to SQL-gen or retrieval — that is the whole
    reason it lives here. While a question is open the asset still serves a
    best-effort answer via the Inference tier (low ``confidence`` + a ``suspect``
    caveat); ``accept_answer`` flips it to ``answered`` once an SME responds.
    """

    question: str
    status: ClarificationStatus = ClarificationStatus.open
    asked_by: str | None = None
    answer: str | None = None
    answered_by: str | None = None
    at: str | None = None


class Audit(BaseModel):
    """Audit tier: never injected into the server context (loader contract).

    Carries ``provenance`` plus free-form ``*_evidence`` prose, hence
    ``extra="allow"``. An optional ``clarification`` records an open question
    about the asset (D12); because the Audit tier is stripped by
    ``Corpus.for_server()``, an open question is never served.
    """

    model_config = ConfigDict(extra="allow")

    provenance: Provenance
    clarification: Clarification | None = None


class Governance(_Strict):
    """Human-authored override, outside the three tiers (D6).

    ``excluded=true`` removes the asset from everything the server sees, in all
    environments, permanently. Distinct from the curator's ``reliability``.
    """

    excluded: bool = False
    reason: str | None = None
    by: str | None = None
    at: str | None = None


class Reliability(_Strict):
    """AI-inferred reliability caveat on a column (curator-authored)."""

    status: ReliabilityStatus = ReliabilityStatus.ok
    note: str | None = None  # prose caveat, server-visible ("UNRELIABLE ...")


# --------------------------------------------------------------------------- #
# Column (inline in a table asset)
# --------------------------------------------------------------------------- #


class Column(_Strict):
    # ── Facts (catalog/data) ──
    physical_name: str
    physical_type: str  # verbatim from catalog, dialect-specific
    logical_type: LogicalType
    nullable: bool
    is_unique: bool
    sample_values: list[Any] = Field(default_factory=list)

    # ── Inference (curator writes / gold fills) ──
    description: str | None = None
    role: ColumnRole | None = None
    references: str | None = None  # column id if FK
    reliability: Reliability = Field(default_factory=Reliability)
    confidence: Confidence | None = None

    # ── Governance (human override) ──
    governance: Governance = Field(default_factory=Governance)

    # ── Audit ──
    audit: Audit | None = None


# --------------------------------------------------------------------------- #
# Typed assets (one YAML file each, except columns which are inline)
# --------------------------------------------------------------------------- #


class TableAsset(_Strict):
    asset_type: Literal["table"] = "table"
    id: str

    # ── Facts ──
    schema: str  # scoping namespace = Postgres/Redshift schema / corpus subtree
    physical_name: str
    row_count: int | None = None

    # ── Inference ──
    description: str | None = None
    grain: str | None = None
    confidence: Confidence | None = None

    columns: list[Column] = Field(default_factory=list)

    # ── Governance (a whole table can be excluded) ──
    governance: Governance = Field(default_factory=Governance)

    # ── Audit ──
    audit: Audit | None = None


class JoinAsset(_Strict):
    asset_type: Literal["join"] = "join"
    id: str

    # ── Facts (referenced physical columns exist in the catalog) ──
    left_table: str
    right_table: str
    on: str  # physical-name equality, e.g. "transaction.CustomerID = customers.CustomerID"

    # ── Inference (the EXISTENCE of the edge is inferred) ──
    cardinality: Cardinality | None = None
    cost: float | None = None  # Steiner-planner input
    confidence: Confidence | None = None

    audit: Audit | None = None


class FewShotAsset(_Strict):
    asset_type: Literal["few_shot"] = "few_shot"
    id: str

    # ── Facts ──
    schema: str

    # ── Inference (curator selects/distills a prompt exemplar) ──
    question: str
    sql: str  # gold SQL in the live (obfuscated) identifiers
    bound_terms: list[str] = Field(default_factory=list)
    complexity: Complexity | None = None
    confidence: Confidence | None = None

    audit: Audit | None = None


class TermBinding(_Strict):
    asset_type: Literal["metric", "table", "column"]
    asset_id: str


class RelatedTerm(_Strict):
    id: str  # another term id
    relation: TermRelation


class TermAsset(_Strict):
    asset_type: Literal["term"] = "term"
    id: str

    # ── Inference (curator maps business language -> assets) ──
    name: str
    synonyms: list[str] = Field(default_factory=list)
    binding: TermBinding | None = None
    related_terms: list[RelatedTerm] = Field(default_factory=list)
    confidence: Confidence | None = None

    audit: Audit | None = None


class MetricRule(BaseModel):
    """A rule inline in a metric (e.g. a filter). Flexible: only ``kind`` fixed."""

    model_config = ConfigDict(extra="allow")

    kind: str  # e.g. "filter"
    note: str | None = None


class MetricAsset(_Strict):
    asset_type: Literal["metric"] = "metric"
    id: str

    # ── Inference (curator derives from evidence + seed queries) ──
    name: str
    base_table: str  # table id
    expression: str  # in meaning; SQL-gen maps to physical
    dimensions: list[str] = Field(default_factory=list)
    rules: list[MetricRule] = Field(default_factory=list)
    confidence: Confidence | None = None

    audit: Audit | None = None


class RuleAsset(_Strict):
    asset_type: Literal["rule"] = "rule"
    id: str

    # ── Inference ──
    kind: RuleKind
    scope: list[str] = Field(default_factory=list)  # asset ids; empty = global
    statement: str
    confidence: Confidence | None = None

    audit: Audit | None = None


class NegativeExampleAsset(_Strict):
    asset_type: Literal["negative_example"] = "negative_example"
    id: str

    # ── Inference (curator proposes; human certifies) ──
    pattern: str
    example_questions: list[str] = Field(default_factory=list)
    reason: str
    escalation: str  # canned escalation blob (D5 refuse-gate)
    confidence: Confidence | None = None

    audit: Audit | None = None


class SkillFrontmatter(BaseModel):
    """Frontmatter of a Markdown skill. The body is prose (not modeled here)."""

    model_config = ConfigDict(extra="allow")

    skill_id: str
    schema: str
    kind: SkillKind
    provenance: Provenance


# --------------------------------------------------------------------------- #
# Discriminated union + parse entry point
# --------------------------------------------------------------------------- #

Asset = Annotated[
    Union[
        TableAsset,
        JoinAsset,
        FewShotAsset,
        TermAsset,
        MetricAsset,
        RuleAsset,
        NegativeExampleAsset,
    ],
    Field(discriminator="asset_type"),
]

_ASSET_ADAPTER: TypeAdapter[Asset] = TypeAdapter(Asset)


def parse_asset(data: dict[str, Any]) -> Asset:
    """Validate a raw YAML mapping into the right typed asset (by ``asset_type``).

    Raises ``pydantic.ValidationError`` on a bad shape, unknown field, or invalid
    enum value.
    """
    return _ASSET_ADAPTER.validate_python(data)


def parse_skill_frontmatter(data: dict[str, Any]) -> SkillFrontmatter:
    return SkillFrontmatter.model_validate(data)
