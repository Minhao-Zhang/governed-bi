"""Layer stack, verdict, and rule table (ADR 0006 §1).

Ordered ``IntEnum``: reaching layer *N* proves ``1..N-1`` passed.
``failed_layer is None`` means passed and nothing else. ``failed_layer`` is
derived from the rule id via :data:`RULES` (except :data:`GUARDRAIL_ERROR`).
``layers_evaluated`` records what ran; its last element is the failing layer.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Mapping, Sequence, TypedDict

from ..register.stages import REFUSED_BY_TO_STAGE, Stage

__all__ = [
    "Layer",
    "CheckVerdict",
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
    """The seven layers, in evaluation order. No fractional members, ever."""

    #: A single read statement that parses under the dialect.
    PARSE = 1
    #: No write, DDL, transaction-control or locking construct anywhere.
    NO_WRITE = 2
    #: Every function call is permitted, and no argument is a whole row (§2).
    FUNCTIONS = 3
    #: Every reference binds to exactly one in-scope source (§4).
    BINDING = 4
    #: Every bound column is allowed, not excluded, not suspect.
    COLUMNS = 5
    #: Every base table is in the licensed set.
    TABLES = 6
    #: Cost / shape estimate. Ships disabled: ``cost_budget`` is UNSET.
    COST = 7


class CheckVerdict(TypedDict):
    """What ``check()`` returns. ``failed_layer is None`` ⟺ ``passed``."""

    passed: bool
    failed_layer: Layer | None
    layers_evaluated: list[Layer]
    #: A rule id from :data:`RULES`, or :data:`PASSED`, or :data:`GUARDRAIL_ERROR`.
    reason_code: str
    #: Free text for the operator and the ledger's dropped tier. Never surfaced.
    detail: str
    #: Every reference → the source it bound to. The single input to the column and
    #: table layers, so the two cannot disagree about what a reference means.
    bound: dict[str, str]


#: The reason code of a passing verdict. A closed vocabulary needs a member for
#: "nothing objected", or the field is empty on the path a reader checks first.
PASSED = "passed"

#: An exception was swallowed inside ``check()``. **Not** in :data:`RULES`: its layer
#: is wherever the exception happened, so it is the one reason code whose layer is
#: contextual. ADR 0006 §12 requires these be *counted* as well as blocked — a
#: systematically broken ``check()`` otherwise presents as an arm that refuses
#: everything, with ``crash_rate == 0`` and every register key present.
GUARDRAIL_ERROR = "guardrail_error"

#: ``refused_by`` for a turn this module refused. Asserted against ``register.stages``
#: at import rather than restated: a literal here that no longer matches a key there
#: is a refusal with no stage.
GUARDRAIL_REFUSED_BY = "guardrail"

#: ``refused_by`` for a turn the input guard refused (§6).
GUARD_REFUSED_BY = "guard"


#: rule id → the layer that owns it. Every rule in the system, once; also the
#: enumeration of legal ``reason_code`` values. Adding a rule here is the point at
#: which someone has to say which layer it belongs to.
RULES: Mapping[str, Layer] = {
    # ── PARSE ──
    "r_unparseable": Layer.PARSE,
    "r_empty_statement": Layer.PARSE,
    "r_multiple_statements": Layer.PARSE,
    #: Raised before NFKC normalisation, at the pipeline's first step: normalising
    #: first folds bidi overrides and zero-width joiners into ordinary text.
    "r_control_characters": Layer.PARSE,
    #: Two declared identifiers differing only by case. The engine folds the
    #: reference to one of them, possibly the decoy; canonicalisation cannot choose.
    "r_ambiguous_fold": Layer.PARSE,
    # ── NO_WRITE ──
    "r_not_a_read": Layer.NO_WRITE,
    "r_write_construct": Layer.NO_WRITE,
    "r_select_into": Layer.NO_WRITE,
    "r_locking_clause": Layer.NO_WRITE,
    # ── FUNCTIONS ──
    "r_function_not_permitted": Layer.FUNCTIONS,
    "r_whole_row_argument": Layer.FUNCTIONS,
    # ── BINDING ──
    "r_unbound_reference": Layer.BINDING,
    "r_ambiguous_reference": Layer.BINDING,
    "r_star_projection": Layer.BINDING,
    "r_natural_join": Layer.BINDING,
    "r_table_function": Layer.BINDING,
    #: A bare name that resolves to a source rather than to a column: in Postgres that
    #: is the whole row as a composite value, with no Column node for anything it
    #: emits. B2 without a function in sight.
    "r_whole_row_reference": Layer.BINDING,
    # ── COLUMNS ──
    "r_column_not_allowed": Layer.COLUMNS,
    "r_column_excluded": Layer.COLUMNS,
    "r_column_suspect": Layer.COLUMNS,
    "r_column_authorization_unavailable": Layer.COLUMNS,
    # ── TABLES ──
    "r_table_not_licensed": Layer.TABLES,
    # ── COST ──
    "r_cost_budget_exceeded": Layer.COST,
}


def rule_layer(rule_id: str) -> Layer:
    """The layer that owns ``rule_id``. Raises on an undeclared rule.

    An undeclared rule would otherwise produce a verdict with no layer, which is B3.
    """
    try:
        return RULES[rule_id]
    except KeyError:
        raise KeyError(
            f"{rule_id!r} is not a declared rule. A verdict must carry the layer that "
            "refused; a rule with no layer would have to report None, which graded "
            "delivery reads as 'passed' (ADR 0006 B3)."
        ) from None


def _evaluated(layers: Sequence[Layer], upto: Layer) -> list[Layer]:
    seen = [layer for layer in layers if layer <= upto]
    if upto not in seen:
        seen.append(upto)
    return seen


def refuse(rule_id: str, detail: str, *, evaluated: Sequence[Layer] = (),
           bound: Mapping[str, str] | None = None) -> CheckVerdict:
    """A blocked verdict. The layer comes from :data:`RULES`, never from a caller."""
    layer = rule_layer(rule_id)
    return CheckVerdict(
        passed=False,
        failed_layer=layer,
        layers_evaluated=_evaluated(evaluated, layer),
        reason_code=rule_id,
        detail=detail,
        bound=dict(bound or {}),
    )


def allow(*, evaluated: Sequence[Layer], bound: Mapping[str, str]) -> CheckVerdict:
    """A passing verdict. There is no way to give it a layer."""
    return CheckVerdict(
        passed=True,
        failed_layer=None,
        layers_evaluated=list(evaluated),
        reason_code=PASSED,
        detail="",
        bound=dict(bound),
    )


def internal_error(layer: Layer, detail: str, *, evaluated: Sequence[Layer] = ()) -> CheckVerdict:
    """Verdict for an exception inside ``check()`` (:data:`GUARDRAIL_ERROR`)."""
    return CheckVerdict(
        passed=False,
        failed_layer=layer,
        layers_evaluated=_evaluated(evaluated, layer),
        reason_code=GUARDRAIL_ERROR,
        detail=detail,
        bound={},
    )


def _assert_stage_vocabulary_is_shared() -> None:
    """Import-time guard: this module's two ``refused_by`` literals still resolve to
    stages in ``register.stages``. A refusal whose stage is unknown cannot be
    attributed to anything.
    """
    for value, expected in ((GUARDRAIL_REFUSED_BY, Stage.check), (GUARD_REFUSED_BY, Stage.guard)):
        actual = REFUSED_BY_TO_STAGE.get(value)
        if actual is not expected:  # pragma: no cover - import-time guard
            raise AssertionError(
                f"refused_by {value!r} maps to {actual!r} in register.stages, expected "
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
