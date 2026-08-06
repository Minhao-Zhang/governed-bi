"""Asset-type policies: identifier fields, tag rules, and retrieval budgets.

Every type has an explicit budget (including ``"all"`` and ``"n/a"``); consumers
may iterate this table, not ``.get`` with a default. Predicates live beside the
types they test, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Mapping

__all__ = [
    "AssetType",
    "TagRule",
    "Budget",
    "AssetPolicy",
    "ASSET_REGISTER",
    "INDEXED_TYPES",
    "CONTEXT_TYPES",
]


class AssetType(str, Enum):
    """The eight. Values match the ``asset_type`` discriminator on the wire."""

    schema = "schema"
    table = "table"
    column = "column"
    join = "join"
    metric = "metric"
    term = "term"
    few_shot = "few_shot"
    negative_example = "negative_example"


class TagRule(str, Enum):
    """How the index derives the schema an asset votes for in ``route``."""

    #: The asset *is* the schema.
    itself = "itself"
    #: Read the asset's own ``schema`` field.
    own_schema = "own_schema"
    #: The schema of the table this column is inline in.
    parent_table = "parent_table"
    #: The schema of ``base_table``.
    base_table = "base_table"
    #: Schema of ``binding`` target; may be absent (untagged, no route vote).
    binding_target = "binding_target"
    #: Schema of ``left_table``. Cross-schema joins vote once (left endpoint).
    left_table = "left_table"
    #: Own ``schema`` if set, otherwise untagged (system-wide).
    own_schema_or_global = "own_schema_or_global"


#: Retrieval budget. ``"all"`` never budgeted; ``"n/a"`` never enters context.
Budget = int | Literal["all", "n/a"]


@dataclass(frozen=True, slots=True)
class AssetPolicy:
    """One row. Every field is required; there are no defaults to inherit."""

    asset_type: AssetType

    #: Model fields that must appear in ``summary``. Empty = no physical id.
    identifier_fields: tuple[str, ...]

    #: How the index tags this asset's schema.
    tag_rule: TagRule

    #: Per-type retrieval budget after the second pass.
    budget: Budget

    #: Whether this type carries ``rules: list[str]`` under ``## Must honour``.
    bears_rules: bool


def _p(
    asset_type: AssetType,
    *,
    identifier_fields: tuple[str, ...],
    tag_rule: TagRule,
    budget: Budget,
    bears_rules: bool = False,
) -> AssetPolicy:
    return AssetPolicy(
        asset_type=asset_type,
        identifier_fields=identifier_fields,
        tag_rule=tag_rule,
        budget=budget,
        bears_rules=bears_rules,
    )


#: The table. Adding an asset type means adding a row here and nowhere else.
ASSET_REGISTER: Mapping[AssetType, AssetPolicy] = {
    AssetType.schema: _p(
        AssetType.schema,
        identifier_fields=("name",),
        tag_rule=TagRule.itself,
        budget="all",
        bears_rules=True,
    ),
    AssetType.table: _p(
        AssetType.table,
        identifier_fields=("physical_name",),
        tag_rule=TagRule.own_schema,
        budget=8,
        bears_rules=True,
    ),
    AssetType.column: _p(
        AssetType.column,
        identifier_fields=("physical_name",),
        tag_rule=TagRule.parent_table,
        budget=30,
    ),
    AssetType.join: _p(
        AssetType.join,
        identifier_fields=("left_table", "right_table"),
        tag_rule=TagRule.left_table,
        budget=5,
    ),
    AssetType.metric: _p(
        AssetType.metric,
        identifier_fields=(),
        tag_rule=TagRule.base_table,
        budget=5,
    ),
    AssetType.term: _p(
        AssetType.term,
        identifier_fields=(),
        tag_rule=TagRule.binding_target,
        budget=5,
    ),
    AssetType.few_shot: _p(
        AssetType.few_shot,
        identifier_fields=(),
        tag_rule=TagRule.own_schema,
        budget=3,
    ),
    AssetType.negative_example: _p(
        AssetType.negative_example,
        identifier_fields=(),
        tag_rule=TagRule.own_schema_or_global,
        budget="n/a",
    ),
}

#: Every type enters the unified index.
INDEXED_TYPES: frozenset[AssetType] = frozenset(ASSET_REGISTER)

#: Types that can reach the prompt (budget != ``"n/a"``).
CONTEXT_TYPES: frozenset[AssetType] = frozenset(
    t for t, p in ASSET_REGISTER.items() if p.budget != "n/a"
)


def _assert_register_is_total() -> None:
    """Import-time: every AssetType has a policy row keyed to itself."""
    missing = set(AssetType) - set(ASSET_REGISTER)
    if missing:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"ASSET_REGISTER is missing rows for: {sorted(t.value for t in missing)}"
        )
    for t, policy in ASSET_REGISTER.items():
        if policy.asset_type is not t:  # pragma: no cover - import-time guard
            raise AssertionError(f"ASSET_REGISTER[{t!r}] declares asset_type={policy.asset_type!r}")


_assert_register_is_total()
