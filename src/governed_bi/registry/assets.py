"""Every asset type has an explicit budget, including ``"all"`` and ``"n/a"``.

Consumers iterate this table rather than calling ``.get`` with a default.
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
]


class AssetType(str, Enum):
    """Values match the ``asset_type`` discriminator on the wire."""

    schema = "schema"
    table = "table"
    column = "column"
    # type: ignore, not a rename: the name collides with ``str.join``, and mypy
    # reads that as shadowing a base method. The wire value is ``join`` in every
    # corpus file, so the name stays; nothing calls ``.join`` on an AssetType.
    join = "join"  # type: ignore[assignment]
    metric = "metric"
    term = "term"
    few_shot = "few_shot"


class TagRule(str, Enum):
    itself = "itself"
    own_schema = "own_schema"
    parent_table = "parent_table"
    base_table = "base_table"
    #: Absent binding: untagged, no route vote.
    binding_target = "binding_target"
    #: Cross-schema joins vote once, from the left endpoint.
    left_table = "left_table"


#: Retrieval budget. ``"all"`` is never budgeted.
Budget = int | Literal["all"]


@dataclass(frozen=True, slots=True)
class AssetPolicy:
    """Every field is required; there are no defaults to inherit."""

    asset_type: AssetType
    identifier_fields: tuple[str, ...]
    tag_rule: TagRule
    budget: Budget
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


#: Adding an asset type means adding a row here and nowhere else.
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
}

INDEXED_TYPES: frozenset[AssetType] = frozenset(ASSET_REGISTER)



def _assert_register_is_total() -> None:
    missing = set(AssetType) - set(ASSET_REGISTER)
    if missing:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"ASSET_REGISTER is missing rows for: {sorted(t.value for t in missing)}"
        )
    for t, policy in ASSET_REGISTER.items():
        if policy.asset_type is not t:  # pragma: no cover - import-time guard
            raise AssertionError(f"ASSET_REGISTER[{t!r}] declares asset_type={policy.asset_type!r}")


_assert_register_is_total()
