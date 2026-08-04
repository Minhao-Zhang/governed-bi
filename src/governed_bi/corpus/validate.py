"""The rules an asset must satisfy, and the record of one that does not.

Three rules, and every one of them comes from a table somewhere else:

============================  ==============================================
rule                          where its values are declared
============================  ==============================================
``1 <= len(summary) <= 250``  ``register/knobs.py`` (``summary_min_chars`` /
                              ``summary_max_chars``)
identifier appears in         ``register/assets.py`` (``identifier_fields``)
``summary``
tag rule satisfiable          ``register/assets.py`` (``tag_rule``) plus the
                              predicate table below
============================  ==============================================

**The numbers are read, not written.** 250 is a knob, and the register's own
docstring says why the value and the comparison are separated: v1 split a threshold
from its comparison and ended up with two ``LOW_CONFIDENCE_JOIN`` constants **with
different operators**, one in the scored artifact and one in the UI reading the same
corpus. A register declares values; the predicate lives once, next to the type it
tests. This module is that one place.

**Why the summary bound is a correctness rule and not a style rule.** The index is a
single shared scoring space. BM25's length normalisation and an embedding's
information density are both relative to the corpus, so one 4,000-character entry
changes what every other entry's score *means*. Over-length is therefore a
validation error and never a truncation -- truncating would silently change the
indexed text, which is the treatment.

**Why there is no rule about ``body``.** Stated as an explicit non-rule because its
absence is load-bearing: ``body`` is optional and unbounded (I2). The seed produces
assets with no body at all, and a validator that required one would falsify ADR
0005's claim that steps 6-9 are measurable with no model -- which is the reason the
seed exists.

**Why the summary rules do not apply uniformly.** Four of the eight types have no
physical identifier: a metric and a term are business concepts, a few-shot's summary
*is* the question, a negative example's *is* the question class. A single blanket
rule would be per-type-skipped in silence, which is the shape of v1's vacuous tests
(L§7). So the four that skip are **named in the register** rather than discovered
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..register.assets import ASSET_REGISTER, AssetType, TagRule
from ..register.knobs import Unset, knob_default

__all__ = ["Problem", "TAG_RULE_FIELDS", "problems_with"]


@dataclass(frozen=True, slots=True)
class Problem:
    """One thing that went wrong with one item, and where.

    Both halves are load-bearing. v1's loader raised on the first unparseable file,
    so one truncated YAML discarded a fully paid 69-schema build with no clue why --
    and the opposite failure is equally real: a *silent* skip turns "a corpus that
    lost half its assets" into "a corpus that merely looks small", and this project
    has already published a result on top of that. A problem a reader cannot act on
    is a silent skip with extra steps, so ``where`` names the file and ``reason``
    names the asset and the rule.

    ``fatal`` is ADR 0008 D9, and it exists because the CLI and the server disagreed:
    ``python -m governed_bi.serve`` exited 3 on **any** problem while ``make_graph()``
    checked nothing, so the CLI refused a corpus the server was happily serving. One
    predicate now decides, and it distinguishes two genuinely different states:

    ``fatal=True``
        An id is not a key. A duplicate id, an asset that did not load, an asset
        reference naming nothing. Retrieval keys on ids, so the corpus is not what it
        claims to be and serving it produces numbers about something else.
    ``fatal=False``
        A **degradation**: recorded, servable, and counted. A few-shot that cannot be
        used, a dimension nobody can resolve, an identifier the corpus cannot carry.
        The corpus is smaller than the lake, and that is a measurement, not a stop.

    Default ``True``, so a new problem site is fatal until somebody decides otherwise.
    Defaulting the other way is how a real defect becomes a warning nobody reads.
    """

    where: str
    reason: str
    fatal: bool = True

    def __str__(self) -> str:
        return f"{self.where}: {self.reason}"


#: How each tag rule is satisfied: the field the index will read, and whether it may
#: be absent.
#:
#: This is a **predicate table, not a copy of the register.** The register says
#: *which* rule an asset type uses; this says what makes that rule answerable, which
#: is a comparison and therefore lives with the code that tests it. Closed at import
#: against :class:`~governed_bi.register.assets.TagRule`, so a ninth rule cannot be
#: added without deciding what satisfies it.
#:
#: The two optional entries are values rather than oversights. An unbound term is
#: **untagged**, and untagged is a state: it does not vote in ``route``, but it is
#: carried into pass two unconditionally and budgeted like anything else. A
#: system-wide negative example is untagged for the same reason. Requiring a schema
#: on either would delete a legitimate asset class.
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

    The *values* come from ``register/knobs.py`` via
    :func:`~governed_bi.register.knobs.knob_default`; what lives here is the
    refusal to accept an ``UNSET`` bound. A knob that ships uncalibrated must not
    become a threshold nobody chose -- that is a fabricated measurement, and a
    validator reading one would be worse than no validator.
    """
    low, high = knob_default("summary_min_chars"), knob_default("summary_max_chars")
    if isinstance(low, Unset) or isinstance(high, Unset):
        raise ValueError(
            "summary_min_chars/summary_max_chars ship UNSET, so there is no bound to "
            "enforce. A guessed cap here would be a fabricated measurement."
        )
    return int(low), int(high)


def problems_with(asset: object) -> list[str]:
    """Every rule ``asset`` breaks, as reasons a reader can act on.

    Empty means valid. Each reason **names the asset**, so a caller that reports the
    bare strings still produces something actionable -- the seed does exactly that.

    Never raises for a malformed asset: an object with no ``summary`` at all reports
    that as a problem. This function is called from a loader that must not raise for
    a bad item.
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
        # The **slug** is what becomes a path component, not the physical name. ADR 0008
        # D1: a key is not a name. Validating the raw identifier here is what made
        # `airline."Air Carriers"` unrepresentable -- the charset rejected it, `table_id`
        # derived the id from it, and the table simply had no asset while 24 few-shots
        # cited it. `physical_name` now carries the engine's spelling verbatim and the
        # rule moves to the string that actually names a file.
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

    So that a producer storing ``left_table`` as a qualified id
    (``beer_factory.customers``) and one storing it as a bare physical name
    (``customers``) both satisfy "the identifier appears in summary". ADR 0005 does
    not settle which of those a join carries, and the reasoning is the one already
    recorded for columns as decision #6: the qualifier is established by the tag
    rule, not by prose, and spending the 250-character budget on it buys nothing a
    reader or the index needs.
    """
    return value.rsplit(".", 1)[-1]


def _assert_every_tag_rule_has_a_predicate() -> None:
    """Import-time closure. A tag rule with no predicate would be silently skipped,
    which is the ``budgets.get(cls, 0)`` shape: the rule exists, something iterates
    the enum, and the missing row becomes a check that never runs."""
    missing = sorted(rule.value for rule in TagRule if rule not in TAG_RULE_FIELDS)
    if missing:  # pragma: no cover - import-time guard
        raise AssertionError(f"TAG_RULE_FIELDS has no predicate for: {missing}")
    unknown = sorted(str(rule) for rule in TAG_RULE_FIELDS if not isinstance(rule, TagRule))
    if unknown:  # pragma: no cover - import-time guard
        raise AssertionError(f"TAG_RULE_FIELDS names things that are not TagRules: {unknown}")


_assert_every_tag_rule_has_a_predicate()
