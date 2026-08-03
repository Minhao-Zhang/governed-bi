"""The eight asset types, and one row of policy each.

Four tables that were four separate documents in v1 are columns of one row here:
which field must appear in ``summary``, how the index derives an asset's schema
tag, its per-type retrieval budget, and which of its fields are exempt from
sanitization. They were separate, they drifted, and two of the drifts were
incidents:

* ``budgets.get(cls, 0)`` silently dropped any type nobody remembered to
  budget — which is why ``NegativeExampleAsset`` was structurally unreachable
  while the very line that dropped it was cited as the reason budgets exist.
  **Here every type has an explicit budget, including the literals ``"all"`` and
  ``"n/a"``, so there is no default to fall through to.** A consumer may iterate
  this table; it may not ``.get`` from it.
* The routing index embedded governance-excluded PII columns while the picker
  summary filtered them — two definitions of "excluded" that drifted because the
  index and the filter were written in different places against different
  tables.

**A register declares values; a predicate lives once, next to the type it tests.**
So this file holds no comparisons. The 250-character cap is a knob in
:mod:`.knobs`; the validator that enforces it is a method in ``corpus.assets``.
v1 split a threshold from its comparison and got two ``LOW_CONFIDENCE_JOIN``
constants **with different operators** — one in the scored artifact, one in the
UI reading the same corpus.
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
    """How the index derives the schema an asset votes for in ``route``.

    Derivation is **not** uniform, which is why it is a declared table rather
    than an attribute read. Three of the eight have no ``schema`` field at all.
    """

    #: The asset *is* the schema.
    itself = "itself"
    #: Read the asset's own ``schema`` field.
    own_schema = "own_schema"
    #: The schema of the table this column is inline in.
    parent_table = "parent_table"
    #: The schema of ``base_table``.
    base_table = "base_table"
    #: The schema of whatever ``binding`` points at. **May be absent** — an unbound
    #: term is untagged, and untagged is a value: it does not vote in ``route``,
    #: but it is carried forward into pass two unconditionally and is budgeted like
    #: anything else. Dropping it would delete a pass-one hit with no record.
    binding_target = "binding_target"
    #: The schema of ``left_table``. A cross-schema join votes **once**, for its
    #: left endpoint, so one edge cannot drag a schema into the top-N twice.
    left_table = "left_table"
    #: The asset's own ``schema`` if set, otherwise untagged (system-wide).
    own_schema_or_global = "own_schema_or_global"


#: A retrieval budget. ``int`` is a count; the two literals are values, not
#: absences, and exist so that no consumer needs a default.
#:
#: ``"all"``
#:     Never budgeted. Every selected schema's ``SchemaAsset`` renders, along with
#:     its ``rules``.
#: ``"n/a"``
#:     Never enters context at all. Consumed by a gate instead — this is
#:     ``negative_example``, and saying so explicitly is what stops it from being
#:     dropped by an implicit zero the way v1's was.
Budget = int | Literal["all", "n/a"]


@dataclass(frozen=True, slots=True)
class AssetPolicy:
    """One row. Every field is required; there are no defaults to inherit."""

    asset_type: AssetType

    #: Model fields whose value must appear in the asset's ``summary``.
    #:
    #: Empty means this type has **no physical identifier**, which is a per-type
    #: fact and not a blanket exemption: a term is a business phrase, a few-shot's
    #: summary *is* the question. v1's vacuous tests are the warning here — a rule
    #: that must apply to all eight but is only evaluable for four will be
    #: per-type-skipped in silence, so the four that skip are named rather than
    #: discovered.
    #:
    #: For ``column`` this is the bare ``physical_name``, not a qualified
    #: ``table.column``. Qualification would spend the 250-character budget on
    #: text the reader does not need: a column's searchability comes from its own
    #: index entry, and its table is established by the tag rule, not by prose.
    identifier_fields: tuple[str, ...]

    #: How the index tags this asset's schema.
    tag_rule: TagRule

    #: Per-type retrieval budget, applied after the second pass over distinct
    #: asset ids ranked by hybrid score.
    budget: Budget

    #: Fields exempt from sanitization, rendered verbatim.
    #:
    #: **Default-deny:** every other string field is sanitized. v1 sanitized note
    #: text only, so a column *description* was the cheaper poisoning vector — the
    #: corpus is writable through an HTTP route and partly model-authored, and the
    #: prompt tells the model this content is authoritative.
    #:
    #: The exemptions are SQL the generator copies character for character.
    #: Sanitizing them mangles quoting that must round-trip, and v1 has a recorded
    #: instance of ``COUNT("Air Carriers"."Code")`` breaking that way.
    verbatim_fields: tuple[str, ...]

    #: Whether this type carries ``rules: list[str]`` — binding prose injected
    #: under ``## Must honour``.
    #:
    #: Only ``schema`` and ``table``. The others are already normative: a metric's
    #: ``expression`` **is** the definition (there is no advisory definition), and
    #: a term's ``binding`` **is** a mandatory mapping. ``rules`` is only needed
    #: where one field could hold either description or obligation.
    bears_rules: bool


def _p(
    asset_type: AssetType,
    *,
    identifier_fields: tuple[str, ...],
    tag_rule: TagRule,
    budget: Budget,
    verbatim_fields: tuple[str, ...] = (),
    bears_rules: bool = False,
) -> AssetPolicy:
    return AssetPolicy(
        asset_type=asset_type,
        identifier_fields=identifier_fields,
        tag_rule=tag_rule,
        budget=budget,
        verbatim_fields=verbatim_fields,
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
        # sample_values is database-derived, not curator prose. Sanitizing it would
        # silently alter a code table — and, because sampled values render into
        # context, would move context_hash for a reason unrelated to the corpus.
        #
        # The cost of the exemption is stated rather than hidden: these values pass
        # to the model unfiltered, so a value containing instruction-shaped text is
        # an indirect-injection surface. That is ADR 0006 §6's recorded gap (data
        # returned by the database bypasses the input guard), and it is closed at
        # the data boundary or not at all. Sanitizing here would corrupt the data
        # without closing it.
        verbatim_fields=("sample_values",),
    ),
    AssetType.join: _p(
        AssetType.join,
        identifier_fields=("left_table", "right_table"),
        tag_rule=TagRule.left_table,
        budget=5,
        verbatim_fields=("on",),
    ),
    AssetType.metric: _p(
        AssetType.metric,
        identifier_fields=(),
        tag_rule=TagRule.base_table,
        budget=5,
        verbatim_fields=("expression",),
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
        verbatim_fields=("sql",),
    ),
    AssetType.negative_example: _p(
        AssetType.negative_example,
        identifier_fields=(),
        tag_rule=TagRule.own_schema_or_global,
        budget="n/a",
    ),
}

#: Every type enters the unified index. Stated as a derived constant rather than a
#: policy column because there is no exception and inviting one would be a
#: mistake: v1's ``JoinAsset`` produced an empty index document, so joins were
#: invisible to both channels and reached context only through grounding.
INDEXED_TYPES: frozenset[AssetType] = frozenset(ASSET_REGISTER)

#: Types that can reach the prompt. ``negative_example`` cannot — it is consumed
#: by a gate. Derived from ``budget``, so the two facts cannot disagree.
CONTEXT_TYPES: frozenset[AssetType] = frozenset(
    t for t, p in ASSET_REGISTER.items() if p.budget != "n/a"
)


def _assert_register_is_total() -> None:
    """Import-time closure check.

    An ``AssetType`` member with no policy row is the ``budgets.get(cls, 0)``
    defect in a new costume: the type exists, something iterates the enum, and the
    missing row becomes a silent zero somewhere downstream.
    """
    missing = set(AssetType) - set(ASSET_REGISTER)
    if missing:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"ASSET_REGISTER is missing rows for: {sorted(t.value for t in missing)}"
        )
    for t, policy in ASSET_REGISTER.items():
        if policy.asset_type is not t:  # pragma: no cover - import-time guard
            raise AssertionError(f"ASSET_REGISTER[{t!r}] declares asset_type={policy.asset_type!r}")


_assert_register_is_total()
