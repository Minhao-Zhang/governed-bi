"""Rules an asset must satisfy, and the record of one that does not.

Thresholds and tag rules are read from ``register`` (not restated). Summary
bounds are correctness (shared scoring space), not style. No ``body`` rule (I2).
Identifier-in-summary applies only to types the register names.
"""


from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..register.assets import ASSET_REGISTER, AssetType, TagRule
from ..register.knobs import Unset, knob_default

__all__ = ["Problem", "TAG_RULE_FIELDS", "problems_with"]


@dataclass(frozen=True, slots=True)
class Problem:
    """One problem with one item: ``where`` names the file, ``reason`` the rule.

    ``fatal=True``: id/key integrity (duplicate, unloadable, dangling ref) — not servable.
    ``fatal=False``: degradation (recorded, servable, counted). ADR 0008 D9.
    """

    where: str
    reason: str
    fatal: bool = True

    def __str__(self) -> str:
        return f"{self.where}: {self.reason}"


#: How each tag rule is satisfied: the field the index will read, and whether that field is
#: **required** (``True`` ⇒ a missing value is a problem). A predicate table, **not** a copy of
#: the register — the register says which rule a type uses, this says what makes that rule
#: answerable. Closed at import against :class:`~governed_bi.register.assets.TagRule`, which
#: declares seven, so an eighth rule cannot be added without deciding what satisfies it.
#:
#: The two optional entries are values, not oversights: an unbound term and a system-wide
#: negative example are **untagged**, which is a state — no vote in ``route``, but carried
#: into pass two unconditionally. Requiring a schema on either deletes a legitimate class.
TAG_RULE_FIELDS: Mapping[TagRule, tuple[str, bool]] = {
    TagRule.itself: ("name", True),
    TagRule.own_schema: ("schema", True),
    TagRule.parent_table: ("parent_table", True),
    TagRule.base_table: ("base_table", True),
    TagRule.left_table: ("left_table", True),
    TagRule.binding_target: ("binding", False),
    TagRule.own_schema_or_global: ("schema", False),
}


def _bounds() -> tuple[int, int]:
    """The inclusive summary length bounds, read from the knob register.

    The values come from ``register/knobs.py``; what lives here is the refusal to accept an
    ``UNSET`` bound, so an uncalibrated knob cannot become a threshold nobody chose.
    """
    low, high = knob_default("summary_min_chars"), knob_default("summary_max_chars")
    if isinstance(low, Unset) or isinstance(high, Unset):
        raise ValueError(
            "summary_min_chars/summary_max_chars ship UNSET, so there is no bound to "
            "enforce. A guessed cap here would be a fabricated measurement."
        )
    return int(low), int(high)


def problems_with(asset: object) -> list[str]:
    """Every rule ``asset`` breaks, as reasons a reader can act on. Empty means valid.

    Each reason **names the asset**, so a caller reporting the bare strings still produces
    something actionable. Never raises for a malformed asset — an object with no ``summary``
    reports that as a problem — because the loader calling it must not raise for a bad item.
    """
    asset_type = getattr(asset, "asset_type", None)
    if not isinstance(asset_type, AssetType):
        return [f"{_name(asset)}: asset_type is {asset_type!r}, not one of the eight"]
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
            "OpenAI returns a vector for it that pollutes the ranking, Bedrock Titan "
            "rejects it and kills the turn"
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

    confidence = getattr(asset, "confidence", None)
    if confidence is not None and (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0.0 <= confidence <= 1.0
    ):
        out.append(
            f"{where}: confidence={confidence!r} is not a number in [0, 1]. This is a "
            "curation-time belief and never an outcome score -- the first thing a "
            "feedback loop will want is to write a hit rate here"
        )

    physical = getattr(asset, "physical_name", None)
    if physical is not None:
        # The **slug** is what becomes a path component, not the physical name (ADR 0008
        # D1: a key is not a name). Validating the raw identifier here is what made
        # `airline."Air Carriers"` unrepresentable -- the charset rejected it, so the table
        # had no asset while 24 few-shots cited it.
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
    """The last dot-separated segment of an identifier.

    So a producer storing ``left_table`` qualified (``beer_factory.customers``) and one
    storing it bare (``customers``) both satisfy "the identifier appears in summary"; ADR
    0005 does not settle which a join carries. Per decision #6, the qualifier comes from the
    tag rule, not from prose, so spending the 250-char budget on it buys nothing.
    """
    return value.rsplit(".", 1)[-1]


def _assert_every_tag_rule_has_a_predicate() -> None:
    """Import-time closure. A tag rule with no predicate here is silently skipped: the rule
    exists, something iterates the enum, and the missing row becomes a check that never
    runs (the ``budgets.get(cls, 0)`` shape)."""
    missing = sorted(rule.value for rule in TagRule if rule not in TAG_RULE_FIELDS)
    if missing:  # pragma: no cover - import-time guard
        raise AssertionError(f"TAG_RULE_FIELDS has no predicate for: {missing}")
    unknown = sorted(str(rule) for rule in TAG_RULE_FIELDS if not isinstance(rule, TagRule))
    if unknown:  # pragma: no cover - import-time guard
        raise AssertionError(f"TAG_RULE_FIELDS names things that are not TagRules: {unknown}")


_assert_every_tag_rule_has_a_predicate()
