"""The eight asset types (ADR 0005 §1).

Common fields on every type: ``id``, ``summary`` (≤250, I1), ``body`` (optional,
I2), ``governance``, ``confidence`` (curation belief, not outcome), ``audit``.
Repeated in each class rather than inherited; import-time assert keeps them
aligned. Validation is :mod:`.validate`, not construction. Mapping conversion is
:mod:`.parse`.
"""


from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, ClassVar

from ..register.assets import ASSET_REGISTER, AssetType

__all__ = [
    "Cardinality",
    "ColumnRole",
    "Complexity",
    "LogicalType",
    "ProvenanceSource",
    "ProvenanceStatus",
    "ReliabilityStatus",
    "TermRelation",
    "Governance",
    "Reliability",
    "Provenance",
    "Audit",
    "Binding",
    "RelatedTerm",
    "SchemaAsset",
    "TableAsset",
    "ColumnAsset",
    "JoinAsset",
    "MetricAsset",
    "TermAsset",
    "FewShotAsset",
    "NegativeExampleAsset",
    "Asset",
    "ASSET_CLASSES",
    "COMMON_FIELDS",
    "class_for",
]


# ── closed vocabularies ───────────────────────────────────────────────────────


class LogicalType(str, Enum):
    """Dialect-independent type. ``physical_type`` keeps the catalog's spelling."""

    string = "string"
    integer = "integer"
    decimal = "decimal"
    date = "date"
    datetime = "datetime"
    boolean = "boolean"


class ColumnRole(str, Enum):
    primary_key = "primary_key"
    foreign_key = "foreign_key"
    key = "key"
    measure = "measure"
    dimension = "dimension"


class ReliabilityStatus(str, Enum):
    """AI-authorable. ``suspect`` argues against a column and the analyst still
    sees it; ``governance.excluded`` removes it, which a person signs for."""

    ok = "ok"
    suspect = "suspect"


class Cardinality(str, Enum):
    one_to_one = "one_to_one"
    one_to_many = "one_to_many"
    many_to_one = "many_to_one"
    many_to_many = "many_to_many"


class Complexity(str, Enum):
    simple = "simple"
    medium = "medium"
    complex = "complex"


class TermRelation(str, Enum):
    synonym_of = "synonym_of"
    broader_than = "broader_than"
    uses = "uses"


class ProvenanceSource(str, Enum):
    curator = "curator"
    gold = "gold"
    human = "human"
    seed = "seed"


class ProvenanceStatus(str, Enum):
    proposed = "proposed"
    draft = "draft"
    certified = "certified"


# ── shared blocks ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Governance:
    """Human-authored override, outside the three tiers (D6).

    ``excluded=True`` removes the asset from everything the analyst sees, in every
    environment. **There is no tool that writes this** -- exclusion is human-only,
    enforced by the absence of a tool plus the phase-boundary guard that re-stamps
    every model-authored ``governance`` block (ADR 0005 §1.5). This class is the
    shape; it is not the enforcement.
    """

    excluded: bool = False
    reason: str | None = None
    by: str | None = None
    at: str | None = None


@dataclass(frozen=True, slots=True)
class Reliability:
    """A caveat on a column. AI-authorable; renders every turn, never budgeted out.

    I3: a relevance cap is exactly what removes a decoy column, because under
    obfuscation a decoy is *designed* to rank low -- and deleting the warning while
    leaving the column reachable is strictly worse than not capping.
    """

    status: ReliabilityStatus = ReliabilityStatus.ok
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Provenance:
    source: ProvenanceSource
    status: ProvenanceStatus
    model: str | None = None
    version: str | None = None
    source_refs: tuple[str, ...] = ()
    built_at: str | None = None


@dataclass(frozen=True, slots=True)
class Audit:
    """Why the inference was made. Never enters the analyst context.

    ``extra`` is the one place unknown keys are kept rather than rejected: evidence
    prose and human-appended provenance vary, and v1's block was ``extra="allow"``
    for that reason. Everywhere else an unknown key is an error, because a mistyped
    field name that parses is a field nobody writes and nothing reads.
    """

    provenance: Provenance | None = None
    evidence: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Binding:
    """What a ``TermAsset`` refers to. **A mandatory mapping, not a description.**

    Renamed from v1's ``TermBinding(asset_type, asset_id)``: a nested field called
    ``asset_type`` shadows the discriminator that decides the *outer* asset's type,
    and one field name meaning two things in one file is the drift this package
    exists to avoid.
    """

    target_type: AssetType
    target_id: str


@dataclass(frozen=True, slots=True)
class RelatedTerm:
    id: str
    relation: TermRelation


#: The six fields every asset carries. Declared once and asserted at import against
#: all eight classes, so the repetition below cannot drift.
COMMON_FIELDS: tuple[str, ...] = ("id", "summary", "body", "governance", "confidence", "audit")


# ── the eight ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SchemaAsset:
    """A namespace. New in v2, and it collapses three problems at once: cross-table
    hard rules get a home, schema routing stops concatenating every table's text,
    and both retrieval levels get a first-class asset."""

    asset_type: ClassVar[AssetType] = AssetType.schema
    id: str
    name: str
    summary: str
    body: str | None = None
    rules: tuple[str, ...] = ()
    governance: Governance = field(default_factory=Governance)
    confidence: float | None = None
    audit: Audit | None = None


@dataclass(frozen=True, slots=True)
class TableAsset:
    """``columns`` holds **derived column ids**, not inline column objects.

    On disk a column lives inline under its table (ADR 0005 §1.2); in memory it is
    its own asset, because the index has one entry per asset and a column that also
    lived inside its table's object would be two copies of one thing. The loader
    expands one into the other and derives the ids.
    """

    asset_type: ClassVar[AssetType] = AssetType.table
    id: str
    schema: str
    physical_name: str
    summary: str
    body: str | None = None
    grain: str | None = None
    row_count: int | None = None
    rules: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    governance: Governance = field(default_factory=Governance)
    confidence: float | None = None
    audit: Audit | None = None


@dataclass(frozen=True, slots=True)
class ColumnAsset:
    """No ``rules``: column-level normative force is ``reliability.status``, which
    has its own render path and works."""

    asset_type: ClassVar[AssetType] = AssetType.column
    id: str
    schema: str
    parent_table: str
    physical_name: str
    summary: str
    body: str | None = None
    physical_type: str | None = None
    logical_type: LogicalType | None = None
    nullable: bool | None = None
    is_unique: bool | None = None
    sample_values: tuple[Any, ...] = ()
    role: ColumnRole | None = None
    references: str | None = None
    reliability: Reliability = field(default_factory=Reliability)
    governance: Governance = field(default_factory=Governance)
    confidence: float | None = None
    audit: Audit | None = None


@dataclass(frozen=True, slots=True)
class JoinAsset:
    """An edge, identified by the **relationship** rather than the table pair.

    No ``schema`` field: the tag rule reads ``left_table``'s schema, and carrying a
    second copy of that fact is how two relationships between one table pair
    collapsed in v1 -- 33 of 57 schemas lost at least one edge before the curator
    ever ran. ADR 0005 §1.2 puts the ON-clause digest in the id for the same
    reason.
    """

    asset_type: ClassVar[AssetType] = AssetType.join
    id: str
    left_table: str
    right_table: str
    on: str
    summary: str
    body: str | None = None
    cardinality: Cardinality | None = None
    cost: float | None = None
    governance: Governance = field(default_factory=Governance)
    confidence: float | None = None
    audit: Audit | None = None


@dataclass(frozen=True, slots=True)
class MetricAsset:
    """Always a business metric: a numeric quantity and a calculation formula.

    ``expression`` is required and is the definition -- there is no advisory
    definition, which is why this type carries no ``rules``.
    """

    asset_type: ClassVar[AssetType] = AssetType.metric
    id: str
    name: str
    base_table: str
    expression: str
    summary: str
    body: str | None = None
    dimensions: tuple[str, ...] = ()
    governance: Governance = field(default_factory=Governance)
    confidence: float | None = None
    audit: Audit | None = None


@dataclass(frozen=True, slots=True)
class TermAsset:
    """Always explanatory: a phrase, and what it refers to.

    ``summary`` must include **all of the aliases**: under I1 only ``summary`` is
    indexed, so synonyms living only in ``synonyms`` or ``body`` would sever the
    term-to-asset bridge -- and that bridge is why every *other* summary may be
    precise instead of keyword-stuffed. A term whose aliases do not fit in 250
    characters splits into two assets sharing a binding (§1.2), rather than
    truncating the thing the asset is for.
    """

    asset_type: ClassVar[AssetType] = AssetType.term
    id: str
    name: str
    summary: str
    body: str | None = None
    synonyms: tuple[str, ...] = ()
    binding: Binding | None = None
    related_terms: tuple[RelatedTerm, ...] = ()
    governance: Governance = field(default_factory=Governance)
    confidence: float | None = None
    audit: Audit | None = None


@dataclass(frozen=True, slots=True)
class FewShotAsset:
    """``summary`` is the question; ``body`` is the question **and** the SQL.

    The repetition is required by I1: the model never sees a summary, so every body
    must be self-contained.
    """

    asset_type: ClassVar[AssetType] = AssetType.few_shot
    id: str
    schema: str
    sql: str
    summary: str
    body: str | None = None
    bound_terms: tuple[str, ...] = ()
    complexity: Complexity | None = None
    governance: Governance = field(default_factory=Governance)
    confidence: float | None = None
    audit: Audit | None = None


@dataclass(frozen=True, slots=True)
class NegativeExampleAsset:
    """One question class per asset. ``schema=None`` means system-wide.

    Empty on BIRD by construction and kept deliberately: it is the only asset whose
    hit is a **decision** rather than a ranking, and its budget is the literal
    ``"n/a"`` precisely so no implicit zero can drop it the way v1's did.
    """

    asset_type: ClassVar[AssetType] = AssetType.negative_example
    id: str
    summary: str
    body: str | None = None
    schema: str | None = None
    governance: Governance = field(default_factory=Governance)
    confidence: float | None = None
    audit: Audit | None = None


Asset = (
    SchemaAsset
    | TableAsset
    | ColumnAsset
    | JoinAsset
    | MetricAsset
    | TermAsset
    | FewShotAsset
    | NegativeExampleAsset
)

#: ``asset_type`` -> class. Keyed on the enum, so a value that is not one of the
#: eight cannot reach a class at all.
ASSET_CLASSES: Mapping[AssetType, type] = {
    AssetType.schema: SchemaAsset,
    AssetType.table: TableAsset,
    AssetType.column: ColumnAsset,
    AssetType.join: JoinAsset,
    AssetType.metric: MetricAsset,
    AssetType.term: TermAsset,
    AssetType.few_shot: FewShotAsset,
    AssetType.negative_example: NegativeExampleAsset,
}


def class_for(asset_type: object) -> type:
    """The class for a wire ``asset_type``. Raises ``ValueError`` on anything else.

    Direct indexing, never ``.get`` with a default: an unrecognised type must stop
    here loudly rather than become an empty something downstream.
    """
    try:
        member = AssetType(asset_type)
    except ValueError:
        known = ", ".join(sorted(t.value for t in AssetType))
        raise ValueError(f"asset_type={asset_type!r} is not one of the eight: {known}") from None
    return ASSET_CLASSES[member]


def _assert_every_asset_carries_the_common_fields() -> None:
    """Import-time closure over the repetition above.

    The six common fields are written out eight times because inheritance orders
    them wrongly. Repetition that nothing checks is exactly the shape that let two
    v1 tables disagree for a year, so a class that forgets ``governance`` -- or a
    ninth type with no policy row -- fails the import instead of the run.
    """
    missing_rows = sorted(t.value for t in ASSET_CLASSES if t not in ASSET_REGISTER)
    if missing_rows:  # pragma: no cover - import-time guard
        raise AssertionError(f"asset classes with no register policy row: {missing_rows}")
    if set(ASSET_CLASSES) != set(AssetType):  # pragma: no cover - import-time guard
        raise AssertionError(
            f"ASSET_CLASSES covers {sorted(t.value for t in ASSET_CLASSES)} but "
            f"AssetType declares {sorted(t.value for t in AssetType)}"
        )
    for asset_type, cls in ASSET_CLASSES.items():
        names = {f.name for f in fields(cls)}
        absent = [f for f in COMMON_FIELDS if f not in names]
        if absent:  # pragma: no cover - import-time guard
            raise AssertionError(f"{cls.__name__} is missing common field(s): {absent}")
        if cls.asset_type is not asset_type:  # pragma: no cover - import-time guard
            raise AssertionError(f"ASSET_CLASSES[{asset_type!r}] holds {cls.__name__}")
        policy = ASSET_REGISTER[asset_type]
        stray = [f for f in policy.identifier_fields if f not in names]
        if stray:  # pragma: no cover - import-time guard
            raise AssertionError(
                f"{cls.__name__}: the register declares identifier field(s) {stray} "
                "that the class does not have, so the rule 'the identifier appears "
                "in summary' would be per-type-skipped in silence"
            )
        if policy.bears_rules != ("rules" in names):  # pragma: no cover - import-time guard
            raise AssertionError(
                f"{cls.__name__}: register says bears_rules={policy.bears_rules} but "
                f"the class {'has' if 'rules' in names else 'has no'} a `rules` field. "
                "Field position is the semantics -- text in `rules` binds, text in "
                "`body` describes -- so a type that carries the field without the "
                "declaration (or the reverse) has two answers to whether its prose "
                "is normative."
            )


_assert_every_asset_carries_the_common_fields()
