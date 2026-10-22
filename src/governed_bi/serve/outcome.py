"""The channels the outcome decision reads. The record projection stays in ``registry/record.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from governed_bi.govern.ledger import ExecutionRecord
from governed_bi.registry.stages import Outcome
from governed_bi.serve.ledger import execution_from_attempts
from governed_bi.serve.state import cleared

__all__ = [
    "RESET_NORMALISED",
    "OutcomeInputs",
    "TurnOutcome",
    "normalised",
]

#: The three channels :data:`~governed_bi.serve.state.PER_TURN_RESET` writes
#: :data:`~governed_bi.serve.state.RESET` into, which :func:`normalised` clears before anything
#: interprets them.
RESET_NORMALISED: tuple[str, ...] = ("path_kind", "failure", "channels")


def normalised(state: Mapping[str, Any]) -> dict[str, Any]:
    """Turn reset sentinels back into absence. Idempotent, so either caller may run first.

    ``failure`` is never written on success, so the bare sentinel looked like a crash on every
    fresh thread's first turn. ``channels`` is not at risk; it stays so the three resets match.
    """
    return {**state, **{k: cleared(state.get(k)) for k in RESET_NORMALISED}}


def _execution(state: Mapping[str, Any]) -> ExecutionRecord:
    """The turn's ledger, including a turn that attempted nothing.

    ``terminal`` comes from the attempts, not from ``path_kind``, so an empty ledger is
    ``no_sql`` whether the turn was blocked, declined, or stubbed.
    """
    existing = state.get("execution")
    if isinstance(existing, Mapping) and "attempts" in existing:
        return existing  # type: ignore[return-value]
    return execution_from_attempts(())  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class OutcomeInputs:
    """Every channel the outcome decision reads. Defaults are an absent channel.

    ``OutcomeInputs()`` is the unmarked turn and classifies as ``crashed``. Not a ``TypedDict``:
    the projection travels with the fields, so ``failure`` is pulled out in one place.
    """

    path_kind: str | None = None
    # A crash with no detail is still a crash. ``failure`` may be a non-mapping.
    failed: bool = False
    failed_stage: str | None = None
    # The exception class, never a traceback.
    error_type: str | None = None
    # ``bool(generated_sql)``. The string is the tool-call arguments, not "answered".
    has_sql: bool = False
    terminal_reason: str | None = None
    # Kept whole. A bool here would turn a non-mapping into ``False`` on every turn.
    guard: Any = None
    clarification_requested: bool = False
    execution: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> OutcomeInputs:
        """The only reader of the state dict in the decision. Calls :func:`normalised`.

        ``execution`` is substituted here: a turn with no ledger gets an empty one, so the
        stub says ``no_sql`` rather than a crash. There is no state left to re-read below.
        """
        state = normalised(state)
        failure = state.get("failure")
        detail = failure if isinstance(failure, Mapping) else {}
        stage = detail.get("stage")
        error_type = detail.get("error_type")
        return cls(
            path_kind=state.get("path_kind"),
            failed=failure is not None,
            failed_stage=stage if isinstance(stage, str) else None,
            error_type=error_type if isinstance(error_type, str) else None,
            has_sql=bool(state.get("generated_sql")),
            terminal_reason=state.get("terminal_reason"),
            guard=state.get("guard"),
            clarification_requested=bool(state.get("clarification_requested")),
            execution=_execution(state),
        )


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """What the decision concluded. ``finish`` translates it and decides nothing.

    ``rail_status`` is carried here, as on :class:`~governed_bi.serve.abstention.AbstentionPatch`:
    the timeline claim belongs to the decision, not the adapter.
    """

    outcome: Outcome
    refused_by: str | None = None
    failed_stage: str | None = None
    error_type: str | None = None
    # System copy on a refusal. Distinct from the model's ``answer_text``.
    text: str | None = None
    # ``classify_turn`` rewrites ``terminal`` on a crash, so this is not the ledger as it arrived.
    execution: Mapping[str, Any] = field(default_factory=dict)
    rail_status: str = "ok"
