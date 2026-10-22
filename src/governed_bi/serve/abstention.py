"""See ADR 0013. Predicates over ``AbstentionInputs``, not a score and not ``ServeState``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable, Literal

from governed_bi.registry.channels import ChannelState
from governed_bi.registry.stages import ABSTENTION_REASONS
from governed_bi.serve.context import EMPTY_CONTEXT
from governed_bi.serve.runtime import bool_setting

__all__ = [
    "ABSTENTION_POLICY",
    "ABSTENTION_RULES",
    "AbstentionInputs",
    "AbstentionPatch",
    "AbstentionRule",
    "abstention_evidence",
    "apply_policy",
    "decide",
]

#: Two configs both named ``abstention_policy`` would compare as one. The enabling setting
#: does not say which policy ran.
ABSTENTION_POLICY = "context_sufficiency_v1"


@dataclass(frozen=True, slots=True)
class AbstentionInputs:
    """Not a ``TypedDict`` in ``state.py``: ``bool_setting`` would import ``retrieve/``.

    ``policy_enabled`` is undeclared because ``input_schema`` drops it.
    """

    #: In the order ``connect`` left them.
    retrieved_tables: tuple[str, ...] = ()
    #: Exactly as the model will be handed it.
    context_block: str = ""
    #: ``failed`` only. ``not_configured`` is not one.
    failed_channels: tuple[str, ...] = ()
    #: Whole tables the char budget dropped after they were retrieved.
    tables_evicted: int = 0
    #: Evidence only; no rule reads it.
    bodies_evicted: int = 0
    schemas: tuple[str, ...] = ()
    #: Evidence and never a rule.
    question_terms_in_corpus: float | None = None
    policy_enabled: bool = False

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> AbstentionInputs:
        """The only state-dict reader. A projection that skips when disabled is a second projection."""
        return cls(
            retrieved_tables=_retrieved_tables(state),
            context_block=_context_block(state),
            failed_channels=_failed_channels(state),
            tables_evicted=_evicted(state, "tables_dropped"),
            bodies_evicted=_evicted(state, "bodies_dropped"),
            schemas=tuple(str(s) for s in (state.get("schemas") or ())),
            question_terms_in_corpus=_lexical_coverage(state),
            policy_enabled=bool_setting(state, "abstention_policy_enabled"),
        )


@dataclass(frozen=True, slots=True)
class AbstentionPatch:
    """``rail_status`` is not derived from ``outcome``: rows while disabled would change the event stream."""

    #: On every turn the node judges or declines to judge.
    result: dict[str, Any]
    #: ``None`` leaves the turn running. Same channel ``route`` and ``connect`` write.
    path_kind: Literal["decline"] | None = None
    #: ``None`` means no row.
    rail_status: Literal["ok", "declined"] | None = None


@dataclass(frozen=True, slots=True)
class AbstentionRule:
    #: Reaches ``terminal_reason``, so the histogram separates it from every other terminal.
    reason: str
    #: Not shown to the model: refusal copy is ``terminal.py``'s.
    why: str
    #: No model, threshold, or state dict.
    fires: Callable[[AbstentionInputs], bool]


def _failed_channels(state: Mapping[str, Any]) -> tuple[str, ...]:
    """``not_configured`` is not a failure: a lexical-only deployment would withhold every turn."""
    out: list[str] = []
    for stage, result in (state.get("channels") or {}).items():
        if not isinstance(result, Mapping):
            continue
        for channel, channel_state in (result.get("channels") or {}).items():
            value = getattr(channel_state, "value", channel_state)
            if str(value) == ChannelState.failed.value:
                out.append(f"{stage}.{channel}")
    return tuple(sorted(out))


def _evicted(state: Mapping[str, Any], field: str) -> int:
    delivery = state.get("delivery")
    if not isinstance(delivery, Mapping):
        return 0
    evicted = delivery.get("evicted")
    if not isinstance(evicted, Mapping):
        return 0
    value = evicted.get(field)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _context_block(state: Mapping[str, Any]) -> str:
    delivery = state.get("delivery")
    if not isinstance(delivery, Mapping):
        return ""
    return str(delivery.get("context_block") or "")


def _retrieved_tables(state: Mapping[str, Any]) -> tuple[str, ...]:
    raw = state.get("retrieved_tables")
    return (
        tuple(str(x) for x in raw)
        if isinstance(raw, Sequence) and not isinstance(raw, str)
        else ()
    )


def _lexical_coverage(state: Mapping[str, Any]) -> float | None:
    """``None`` and not ``0.0`` when the turn did not measure it: an unmeasured share and a
    measured zero want opposite readings, and ``0`` is a measurement."""
    retrieved = state.get("retrieved")
    coverage = retrieved.get("lexical_coverage") if isinstance(retrieved, Mapping) else None
    if isinstance(coverage, (int, float)) and not isinstance(coverage, bool):
        return float(coverage)
    return None


#: First match wins, causes before consequences, so the reason names what a person can fix.
#: A failed channel often also retrieved nothing; ``nothing_retrieved`` would send them to the
#: corpus for a provider outage. Then widest-first: no tables, empty context, then a drop.
ABSTENTION_RULES: tuple[AbstentionRule, ...] = (
    AbstentionRule(
        reason="retrieval_channel_failed",
        why=(
            "a retrieval channel that was configured to run errored, so the tables this turn "
            "worked from were chosen by a retriever that is not the declared one. Answering "
            "would record the declared treatment and deliver a different one"
        ),
        fires=lambda inputs: bool(inputs.failed_channels),
    ),
    AbstentionRule(
        reason="nothing_retrieved",
        why=(
            "retrieval found no table, so every statement the agent can write names a "
            "relation Layer 6 will refuse. The five run_query attempts would end where connect "
            "already ended, and the refusal would be attributed to the layer stack rather than "
            "to the empty shortlist that caused it"
        ),
        fires=lambda inputs: not inputs.retrieved_tables,
    ),
    AbstentionRule(
        reason="empty_context",
        why=(
            "the rendered context is empty, so the model has been handed the question and "
            "nothing else. Any statement it writes is invented rather than grounded"
        ),
        fires=lambda inputs: inputs.context_block.strip() in ("", EMPTY_CONTEXT),
    ),
    AbstentionRule(
        reason="retrieved_table_dropped",
        why=(
            "the character budget dropped a whole retrieved table before the model saw it, so "
            "this turn is asking for SQL over a relation it did not show. A table can be "
            "routed, retrieved, counted as covered and then evicted; `evicted` is the only "
            "record that it happened"
        ),
        fires=lambda inputs: inputs.tables_evicted > 0,
    ),
)


def abstention_evidence(inputs: AbstentionInputs) -> dict[str, Any]:
    """See ADR 0013. The table-id list is capped; the count beside it is not, so the cap cannot hide the size."""
    return {
        "n_retrieved_tables": len(inputs.retrieved_tables),
        "retrieved_tables": sorted(inputs.retrieved_tables)[:_EVIDENCE_LIST_CAP],
        "schemas": list(inputs.schemas),
        "context_chars": len(inputs.context_block),
        "tables_evicted": inputs.tables_evicted,
        "bodies_evicted": inputs.bodies_evicted,
        "failed_channels": list(inputs.failed_channels),
        "question_terms_in_corpus": inputs.question_terms_in_corpus,
    }


#: The record's ``retrieved_tables`` stays complete.
_EVIDENCE_LIST_CAP = 12


def decide(inputs: AbstentionInputs) -> dict[str, Any]:
    """Recomputable from the artifact: no model, clock, environment, network, or state dict."""
    for rule in ABSTENTION_RULES:
        if rule.fires(inputs):
            return {
                "policy": ABSTENTION_POLICY,
                "outcome": "withhold",
                "reason": rule.reason,
                # Every rule up to and including the one that fired. The ones after it were
                # never asked, and listing them would claim a check that did not happen.
                "rules_evaluated": [
                    r.reason
                    for r in ABSTENTION_RULES[: ABSTENTION_RULES.index(rule) + 1]
                ],
                "evidence": abstention_evidence(inputs),
            }
    return {
        "policy": ABSTENTION_POLICY,
        "outcome": "answer",
        "reason": None,
        "rules_evaluated": [r.reason for r in ABSTENTION_RULES],
        "evidence": abstention_evidence(inputs),
    }


#: No evidence: facts behind a decision nobody took have no reader.
def _disabled() -> dict[str, Any]:
    return {
        "policy": ABSTENTION_POLICY,
        "outcome": "disabled",
        "reason": None,
        "rules_evaluated": [],
        "evidence": {},
    }


def apply_policy(inputs: AbstentionInputs) -> AbstentionPatch:
    """The adapter has no branch of its own: a condition there is reachable only by invoking a graph."""
    if not inputs.policy_enabled:
        return AbstentionPatch(result=_disabled())
    result = decide(inputs)
    if result["outcome"] != "withhold":
        return AbstentionPatch(result=result, rail_status="ok")
    return AbstentionPatch(result=result, path_kind="decline", rail_status="declined")


def _assert_the_policy_speaks_the_declared_vocabulary() -> None:
    """An undeclared reason is unattributable; a reason with no rule reads as a live decision."""
    reasons = [rule.reason for rule in ABSTENTION_RULES]
    if len(reasons) != len(set(reasons)):  # pragma: no cover - import-time guard
        raise AssertionError(f"two abstention rules share a reason: {sorted(reasons)}")
    if set(reasons) != ABSTENTION_REASONS:  # pragma: no cover - import-time guard
        raise AssertionError(
            "the policy's rules and registry.stages.ABSTENTION_REASONS disagree: "
            f"rules-only={sorted(set(reasons) - ABSTENTION_REASONS)}, "
            f"declared-only={sorted(ABSTENTION_REASONS - set(reasons))}"
        )


_assert_the_policy_speaks_the_declared_vocabulary()
