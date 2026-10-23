"""Asset rules. Bounds and tag rules are read from the register, not copied here.

There is no body-length rule. Identifier-in-summary follows the types the register names.
"""


from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..registry.assets import ASSET_REGISTER, AssetType, TagRule
from ..registry.settings import Unset, setting_default
from .schema import ColumnAsset, JoinAsset, MetricAsset, SchemaAsset, TableAsset

__all__ = ["Problem", "TAG_RULE_FIELDS", "problems_with"]


@dataclass(frozen=True, slots=True)
class Problem:
    """``fatal`` is id integrity and is not servable. Otherwise the problem is recorded. See ADR 0008."""

    where: str
    reason: str
    fatal: bool = True

    def __str__(self) -> str:
        return f"{self.where}: {self.reason}"


#: Field, and whether a missing value is a problem. Closed against ``TagRule`` at import.
#: ``binding`` is optional: an unbound term stays untagged.
TAG_RULE_FIELDS: Mapping[TagRule, tuple[str, bool]] = {
    TagRule.itself: ("name", True),
    TagRule.own_schema: ("schema", True),
    TagRule.parent_table: ("parent_table", True),
    TagRule.base_table: ("base_table", True),
    TagRule.left_table: ("left_table", True),
    TagRule.binding_target: ("binding", False),
}


def _bounds() -> tuple[int, int]:
    """Inclusive summary bounds. An unset bound is refused, so a threshold nobody chose cannot apply."""
    low, high = setting_default("summary_min_chars"), setting_default("summary_max_chars")
    if isinstance(low, Unset) or isinstance(high, Unset):
        raise ValueError(
            "summary_min_chars/summary_max_chars ship UNSET, so there is no bound to "
            "enforce. A guessed cap here would be a fabricated measurement."
        )
    return int(low), int(high)


def problems_with(asset: object) -> list[str]:
    """Reasons ``asset`` breaks. Never raises: a bad item is a problem, not a crash."""
    asset_type = getattr(asset, "asset_type", None)
    if not isinstance(asset_type, AssetType):
        return [f"{_name(asset)}: asset_type is {asset_type!r}, not a known asset type"]
    policy = ASSET_REGISTER[asset_type]
    where = _name(asset)
    out: list[str] = []

    summary = getattr(asset, "summary", None)
    if not isinstance(summary, str):
        return [f"{where}: summary is {summary!r}; every asset must carry indexable text"]

    low, high = _bounds()
    if len(summary.strip()) < low:
        out.append(
            f"{where}: summary is empty. A blank document is a live provider hazard -- "
            "the embedder returns a vector for it that pollutes the ranking"
        )
    elif len(summary) > high:
        out.append(
            f"{where}: summary is {len(summary)} characters, over the {high} cap. The "
            "index is one shared scoring space, so an oversized entry changes what "
            "every other entry's score means. Rewrite it; do not truncate -- the "
            "indexed text is the treatment"
        )

    haystack = summary.casefold()
    for name in policy.identifier_fields:
        value = getattr(asset, name, None)
        if not isinstance(value, str) or not value:
            out.append(f"{where}: {name} is {value!r}, and the register makes it this type's identifier")
            continue
        if _bare(value).casefold() not in haystack:
            out.append(
                f"{where}: summary does not contain {name}={value!r}. Only summary is "
                "indexed, so an asset whose own identifier is absent from it cannot be "
                "found by the name it is known by"
            )

    field_name, required = TAG_RULE_FIELDS[policy.tag_rule]
    if required and not getattr(asset, field_name, None):
        out.append(
            f"{where}: {field_name} is missing, and the index derives this type's schema "
            f"tag from it ({policy.tag_rule.value}). An untagged asset of this type does "
            "not vote in route and there is no rule saying it should not"
        )

    # The Ossie file joins summary and body with a blank line and rules with newlines (ADR 0022).
    joined = (SchemaAsset, TableAsset, ColumnAsset, JoinAsset, MetricAsset)
    if isinstance(asset, joined) and "\n" in asset.summary:
        out.append(f"{where}: summary contains a newline; it must be one paragraph")
    for rule in getattr(asset, "rules", ()):
        if "\n" in rule:
            out.append(f"{where}: a rule contains a newline; write each rule on one line")

    physical = getattr(asset, "physical_name", None)
    if physical is not None:
        # Check the slug, not the engine name: the slug is the path component (ADR 0008).
        from .identity import UnsafeName, slug, validate_path_component

        try:
            validate_path_component(slug(physical), what="slug(physical_name)")
        except UnsafeName as err:
            out.append(
                f"{where}: {err}. The slug derived from physical_name={physical!r} is "
                "what names a file and keys the index, so it must be a bare identifier "
                "(ADR 0008 D1); the physical name itself may be anything the engine has"
            )

    return out


def _name(asset: object) -> str:
    identifier = getattr(asset, "id", None)
    return identifier if isinstance(identifier, str) and identifier else f"<{type(asset).__name__}>"


def _bare(value: str) -> str:
    """Last segment only, so a qualified id and a bare one both count as present in the summary."""
    return value.rsplit(".", 1)[-1]


def _assert_every_tag_rule_has_a_predicate() -> None:
    """A tag rule missing here would be a check that never runs."""
    missing = sorted(rule.value for rule in TagRule if rule not in TAG_RULE_FIELDS)
    if missing:  # pragma: no cover - import-time guard
        raise AssertionError(f"TAG_RULE_FIELDS has no predicate for: {missing}")
    unknown = sorted(str(rule) for rule in TAG_RULE_FIELDS if not isinstance(rule, TagRule))
    if unknown:  # pragma: no cover - import-time guard
        raise AssertionError(f"TAG_RULE_FIELDS names things that are not TagRules: {unknown}")


_assert_every_tag_rule_has_a_predicate()
