"""The seven asset types. See ADR 0005.

Common fields are repeated on each class: a shared base would order them wrongly. An import check keeps the copies
aligned.
"""


from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, ClassVar

from ..registry.assets import ASSET_REGISTER, AssetType

__all__ = [
    "Cardinality",
    "ColumnRole",
    "LogicalType",
    "ProvenanceSource",
    "ProvenanceStatus",
    "ReliabilityStatus",
    "Governance",
    "Reliability",
    "Provenance",
    "Audit",
    "Binding",
    "SchemaAsset",
    "TableAsset",
    "ColumnAsset",
    "JoinAsset",
    "MetricAsset",
    "TermAsset",
    "FewShotAsset",
    "Asset",
    "ASSET_CLASSES",
    "COMMON_FIELDS",
    "class_for",
]


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
    """``suspect`` still shows the column. Removal is ``governance.excluded``, signed by a person."""

    ok = "ok"
    suspect = "suspect"


class Cardinality(str, Enum):
    one_to_one = "one_to_one"
    one_to_many = "one_to_many"
    many_to_one = "many_to_one"
    many_to_many = "many_to_many"






class ProvenanceSource(str, Enum):
    curator = "curator"
    gold = "gold"
    human = "human"
    seed = "seed"


class ProvenanceStatus(str, Enum):
    proposed = "proposed"
    draft = "draft"
    certified = "certified"


@dataclass(frozen=True, slots=True)
class Governance:
    """Human exclusion. ``excluded`` hides the asset from the analyst.

    The class is the shape. It does not decide who may set the flag.
    """

    excluded: bool = False
    reason: str | None = None
    by: str | None = None
    at: str | None = None


@dataclass(frozen=True, slots=True)
class Reliability:
    """A caveat rendered on every hit. A relevance cap would drop the warning and leave the column."""

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
    """Why an inference was made. Stays out of the analyst context.

    ``extra`` keeps unknown keys. Everywhere else an unknown key is an error.
    """

    provenance: Provenance | None = None
    evidence: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Binding:
    """What a term refers to. Fields are ``target_*`` so a nested ``asset_type`` cannot shadow the outer one."""

    target_type: AssetType
    target_id: str




#: Fields every asset carries. Asserted at import so the copies cannot drift.
COMMON_FIELDS: tuple[str, ...] = ("id", "summary", "body", "governance", "audit")


@dataclass(frozen=True, slots=True)
class SchemaAsset:
    asset_type: ClassVar[AssetType] = AssetType.schema
    id: str
    name: str
    summary: str
    body: str | None = None
    rules: tuple[str, ...] = ()
    governance: Governance = field(default_factory=Governance)
    audit: Audit | None = None


@dataclass(frozen=True, slots=True)
class TableAsset:
    """``columns`` holds derived ids, in the order of the dataset's fields on disk. See ADR 0022."""

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
    audit: Audit | None = None


@dataclass(frozen=True, slots=True)
class ColumnAsset:
    """No ``rules`` field: column force is ``reliability.status``."""

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
    role: ColumnRole | None = None
    reliability: Reliability = field(default_factory=Reliability)
    governance: Governance = field(default_factory=Governance)
    audit: Audit | None = None


@dataclass(frozen=True, slots=True)
class JoinAsset:
    """Identified by the relationship, not the table pair. No ``schema`` field: the tag reads ``left_table``. See ADR
    0005."""

    asset_type: ClassVar[AssetType] = AssetType.join
    id: str
    left_table: str
    right_table: str
    on: str
    summary: str
    body: str | None = None
    cardinality: Cardinality | None = None
    governance: Governance = field(default_factory=Governance)
    audit: Audit | None = None


@dataclass(frozen=True, slots=True)
class MetricAsset:
    """A quantity plus ``expression``. No ``rules``: the expression is the definition."""

    asset_type: ClassVar[AssetType] = AssetType.metric
    id: str
    name: str
    base_table: str
    expression: str
    summary: str
    body: str | None = None
    dimensions: tuple[str, ...] = ()
    governance: Governance = field(default_factory=Governance)
    audit: Audit | None = None


@dataclass(frozen=True, slots=True)
class TermAsset:
    """Aliases belong in ``summary``: only summary is indexed, so synonyms elsewhere are invisible."""

    asset_type: ClassVar[AssetType] = AssetType.term
    id: str
    name: str
    summary: str
    body: str | None = None
    synonyms: tuple[str, ...] = ()
    binding: Binding | None = None
    governance: Governance = field(default_factory=Governance)
    audit: Audit | None = None


@dataclass(frozen=True, slots=True)
class FewShotAsset:
    """``body`` is derived from the question and the SQL, never stored. The hit text has to stand alone."""

    asset_type: ClassVar[AssetType] = AssetType.few_shot
    id: str
    schema: str
    sql: str
    summary: str
    body: str | None = field(init=False, default=None)
    governance: Governance = field(default_factory=Governance)
    audit: Audit | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "body", f"Question: {self.summary}\nSQL:\n{self.sql}")


Asset = (
    SchemaAsset
    | TableAsset
    | ColumnAsset
    | JoinAsset
    | MetricAsset
    | TermAsset
    | FewShotAsset
)

#: Enum to class. An unknown value cannot reach a class.
ASSET_CLASSES: Mapping[AssetType, type[Asset]] = {
    AssetType.schema: SchemaAsset,
    AssetType.table: TableAsset,
    AssetType.column: ColumnAsset,
    AssetType.join: JoinAsset,
    AssetType.metric: MetricAsset,
    AssetType.term: TermAsset,
    AssetType.few_shot: FewShotAsset,
}


def class_for(asset_type: object) -> type:
    """Class for a wire ``asset_type``. An unknown type raises; there is no default class."""
    try:
        member = AssetType(asset_type)
    except ValueError:
        known = ", ".join(sorted(t.value for t in AssetType))
        raise ValueError(f"asset_type={asset_type!r} is not one of: {known}") from None
    return ASSET_CLASSES[member]


def _assert_every_asset_carries_the_common_fields() -> None:
    """The five common fields are copied per class because a base class would order them wrongly."""
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
