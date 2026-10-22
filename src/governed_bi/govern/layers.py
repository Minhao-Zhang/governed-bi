"""Layer stack and rule table. See ADR 0006. Reaching layer N proves 1..N-1 passed. ``failed_layer is None`` means
passed. The layer comes from :data:`RULES`, except :data:`GUARDRAIL_ERROR`."""

from __future__ import annotations

from enum import IntEnum
from typing import Mapping, Sequence, TypedDict

from ..registry.stages import REFUSED_BY_TO_STAGE, Stage

__all__ = [
    "Layer",
    "CheckResult",
    "RULES",
    "PASSED",
    "GUARDRAIL_ERROR",
    "GUARDRAIL_REFUSED_BY",
    "GUARD_REFUSED_BY",
    "rule_layer",
    "refuse",
    "allow",
    "internal_error",
]


class Layer(IntEnum):
    """The six layers, in evaluation order. No fractional members."""

    PARSE = 1
    NO_WRITE = 2
    FUNCTIONS = 3
    BINDING = 4
    COLUMNS = 5
    TABLES = 6


class CheckResult(TypedDict):
    """``failed_layer is None`` if and only if ``passed``."""

    passed: bool
    failed_layer: Layer | None
    layers_evaluated: list[Layer]
    #: A rule id from :data:`RULES`, or :data:`PASSED`, or :data:`GUARDRAIL_ERROR`.
    reason_code: str
    #: Operator text. Never surfaced.
    detail: str
    #: Every reference to the source it bound to. The only input of the column and table
    #: layers, so the two cannot disagree about what a reference means.
    bound: dict[str, str]


#: A closed vocabulary needs a member for "nothing objected", or the passing path is blank.
PASSED = "passed"

#: An exception swallowed inside ``check()``. Not in :data:`RULES`: the layer is wherever
#: it happened. Counted as well as blocked, or a broken ``check()`` looks like a config
#: that refuses everything. See ADR 0006.
GUARDRAIL_ERROR = "guardrail_error"

#: ``refused_by`` for a turn this module refused. Asserted against ``registry.stages``
#: at import: a literal that does not match a key there is a refusal with no stage.
GUARDRAIL_REFUSED_BY = "guardrail"

#: ``refused_by`` for a turn the input guard refused.
GUARD_REFUSED_BY = "guard"


#: Rule id to the layer that owns it. Adding a rule here is where its layer is chosen.
RULES: Mapping[str, Layer] = {
    "r_unparseable": Layer.PARSE,
    "r_empty_statement": Layer.PARSE,
    "r_multiple_statements": Layer.PARSE,
    #: Before NFKC: normalising first folds bidi overrides into ordinary text.
    "r_control_characters": Layer.PARSE,
    #: Two declared identifiers differ only by case. The engine may fold to the decoy.
    "r_ambiguous_fold": Layer.PARSE,
    "r_not_a_read": Layer.NO_WRITE,
    "r_write_construct": Layer.NO_WRITE,
    "r_select_into": Layer.NO_WRITE,
    "r_locking_clause": Layer.NO_WRITE,
    "r_function_not_permitted": Layer.FUNCTIONS,
    "r_whole_row_argument": Layer.FUNCTIONS,
    "r_unbound_reference": Layer.BINDING,
    "r_ambiguous_reference": Layer.BINDING,
    "r_star_projection": Layer.BINDING,
    "r_natural_join": Layer.BINDING,
    "r_table_function": Layer.BINDING,
    #: A bare name of a source, not a column: the whole row, with no Column node. B2.
    "r_whole_row_reference": Layer.BINDING,
    "r_column_not_allowed": Layer.COLUMNS,
    "r_column_excluded": Layer.COLUMNS,
    "r_column_suspect": Layer.COLUMNS,
    #: This turn's retrieved set does not include the table.
    "r_table_not_retrieved": Layer.TABLES,
}


def rule_layer(rule_id: str) -> Layer:
    """Raises on an undeclared rule: a missing layer is what graded delivery reads as passed. See ADR 0006."""
    try:
        return RULES[rule_id]
    except KeyError:
        raise KeyError(
            f"{rule_id!r} is not a declared rule. A result must carry the layer that "
            "refused; a rule with no layer would have to report None, which graded "
            "delivery reads as 'passed' (ADR 0006 B3)."
        ) from None


def _evaluated(layers: Sequence[Layer], upto: Layer) -> list[Layer]:
    seen = [layer for layer in layers if layer <= upto]
    if upto not in seen:
        seen.append(upto)
    return seen


def refuse(rule_id: str, detail: str, *, evaluated: Sequence[Layer] = (),
           bound: Mapping[str, str] | None = None) -> CheckResult:
    """A blocked result. The layer comes from :data:`RULES`, never from a caller."""
    layer = rule_layer(rule_id)
    return CheckResult(
        passed=False,
        failed_layer=layer,
        layers_evaluated=_evaluated(evaluated, layer),
        reason_code=rule_id,
        detail=detail,
        bound=dict(bound or {}),
    )


def allow(*, evaluated: Sequence[Layer], bound: Mapping[str, str]) -> CheckResult:
    """A passing result. There is no way to give it a failed layer."""
    return CheckResult(
        passed=True,
        failed_layer=None,
        layers_evaluated=list(evaluated),
        reason_code=PASSED,
        detail="",
        bound=dict(bound),
    )


def internal_error(layer: Layer, detail: str, *, evaluated: Sequence[Layer] = ()) -> CheckResult:
    """An exception inside ``check()``. The layer is the one that was running."""
    return CheckResult(
        passed=False,
        failed_layer=layer,
        layers_evaluated=_evaluated(evaluated, layer),
        reason_code=GUARDRAIL_ERROR,
        detail=detail,
        bound={},
    )


def _assert_stage_vocabulary_is_shared() -> None:
    """These ``refused_by`` literals must resolve in ``registry.stages``. :data:`GUARDRAIL_ERROR` is one of them: a
    drift records a swallowed exception as an ordinary refusal."""
    for value, expected in (
        (GUARDRAIL_REFUSED_BY, Stage.check),
        (GUARD_REFUSED_BY, Stage.guard),
        (GUARDRAIL_ERROR, Stage.check),
    ):
        actual = REFUSED_BY_TO_STAGE.get(value)
        if actual is not expected:  # pragma: no cover - import-time guard
            raise AssertionError(
                f"refused_by {value!r} maps to {actual!r} in registry.stages, expected "
                f"{expected!r}. A governance refusal with the wrong stage is a refusal "
                "attributed to the wrong part of the system."
            )

    contextual = set(RULES) & {PASSED, GUARDRAIL_ERROR}
    if contextual:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"{sorted(contextual)} must not be in RULES: their layer is contextual, and "
            "a fixed layer for them would report the wrong one."
        )


_assert_stage_vocabulary_is_shared()
