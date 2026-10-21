"""The channels ``finish`` reads when it decides what happened to the turn.

The projection's other fields are ``registry/record.py``, walked by name, so they
are not copied here. The derivation stays in ``serve/nodes/finish.py`` beside that
projection. The node writes ``answer`` and one ``final`` stage event.
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
    """``state`` with the reset sentinels turned back into absence.

    ``Session.turn`` writes :data:`~governed_bi.serve.state.RESET` to ``path_kind``, ``failure``
    and ``channels``, and the first two must be normalised before they are read: their annotations
    are Unions, so the channel seeds ``MISSING`` and LangGraph assigns the first write raw (see
    :func:`~governed_bi.serve.state.cleared`). ``failure`` is the one that bites — a successful
    turn never writes it, so the bare sentinel made ``state.get("failure") is not None`` true on
    every successful first turn of a fresh thread. ``channels`` strips to ``dict`` and is never at
    risk; it stays in the tuple for symmetry.

    Normalised for ``finish`` rather than in each reader because ``finish`` is the only node that
    *interprets* these channels — every other reader compares them against known values, where
    an unrecognised string already behaves as "not terminal".

    **Idempotent**, which is what lets :meth:`OutcomeInputs.from_state` call it on its own input
    while ``finish`` also calls it for the record projection: :func:`~.state.cleared` maps an
    already-cleared value to itself. Two callers, one normalisation, and neither has to trust
    that the other ran first.
    """
    return {**state, **{k: cleared(state.get(k)) for k in RESET_NORMALISED}}


def _execution(state: Mapping[str, Any]) -> ExecutionRecord:
    """The turn's ``ExecutionRecord``, written on every path including "no SQL".

    ``terminal`` is never derived here from ``path_kind``: ``execution_from_attempts`` is the
    one derivation and it reads the attempts, so a turn that attempted nothing says ``no_sql``
    whether it was guard-blocked, declined or stubbed.
    """
    existing = state.get("execution")
    if isinstance(existing, Mapping) and "attempts" in existing:
        return existing  # type: ignore[return-value]
    return execution_from_attempts(())  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class OutcomeInputs:
    """**Every channel the outcome decision reads, and the whole of it.**

    Seven: ``path_kind``, ``failure`` (as the three facts anything asks of it), ``generated_sql``
    (as ``has_sql``), ``terminal_reason``, ``guard``, ``clarification_requested``, and
    ``execution``. The module docstring says why the other 25 of ``finish``'s 32 reads are not
    here and must not be.

    **Every field has a default**, so a test states the two or three facts its case is about
    rather than assembling a turn — the property this boundary exists for. Every default is the value
    an absent channel projects to, so ``OutcomeInputs()`` is the unmarked turn: no path, no
    failure, no statement, no ledger. That is the case
    :func:`~governed_bi.registry.stages.classify_outcome` reads as ``crashed``, and it is the
     reading of a turn nothing observed ending.

    Not a ``TypedDict`` beside ``ServeState``: this is a *read* view, so the projection has to
    travel with it or there are two places that know how to pull ``failure``'s stage out of a
    state dict.
    """

    #: ``path_kind`` after :func:`normalised`. The route the turn ended on, or ``None``.
    path_kind: str | None = None
    #: Whether ``failure`` is present after :func:`normalised`. A separate field from the two
    #: below because a crash with no detail is still a crash: ``failure`` can be a non-mapping,
    #: and the presence and the contents were read through different tests before the split.
    failed: bool = False
    #: ``failure["stage"]`` when it is a string. The stage that raised.
    failed_stage: str | None = None
    #: ``failure["error_type"]`` when it is a string. The exception **class**, never a traceback.
    error_type: str | None = None
    #: ``bool(generated_sql)``. Never enough on its own to mean "answered" — it comes from the
    #: tool-call *arguments*, so producing a string once counted as producing an answer.
    has_sql: bool = False
    #: The reason a ``refuse`` or ``decline`` gives, when the node that ended the turn named one.
    terminal_reason: str | None = None
    #: The ``guard`` channel as it stands. **Not** narrowed to a ``guard_blocked`` boolean here,
    #: which was the first shape of this field and is wrong twice over: the fallback that reads
    #: it runs only on a ``refuse`` with no named reason, so a boolean computed in the projection
    #: would run on every turn instead — and ``guard`` is declared ``Absence.never``, so a
    #: malformed one is a wiring failure. Narrowed eagerly it becomes ``False`` and the turn is
    #: attributed to ``negative_example``: a wrong reason in place of a loud one. Interpreted
    #: where it is used, a malformed ``guard`` raises on the one path that asks, which is the
    #: behaviour that was there before the split.
    guard: Any = None
    #: Whether the turn asked the reader a question instead of answering.
    clarification_requested: bool = False
    #: The turn's ledger, after the substitution :func:`_execution` documents. Already a declared
    #: type (:class:`~governed_bi.govern.ledger.ExecutionRecord`), so it travels whole rather
    #: than being flattened into three more fields.
    execution: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> OutcomeInputs:
        """**The only reader of the state dict in the decision.**

        One projection, so there is one answer to "where does ``terminal`` come from". Two
        functions that both knew is the defect this boundary is against: a second reader of a channel
        is where ``measure/checks.py`` came to evidence "reached finish" on
        ``Outcome.clarification`` and silently drop rows from a denominator.

        Calls :func:`normalised` on its input, which is idempotent, so the view is right whether
        or not the caller normalised first.

        **``execution`` is substituted here and nowhere else.** A turn nothing wrote a ledger for
        — the ``--no-model`` stub is one — gets ``execution_from_attempts(())``, so it says
        ``no_sql`` rather than reading as a crash. That substitution used to be passed down to the
        derivation explicitly, under a paragraph warning the next reader not to re-read
        ``state["execution"]`` and see the ``None`` it exists to replace. There is no ``state``
        down there to re-read now, so the warning is a type.
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
    """What the decision concluded — one field per thing the record or the timeline reports.

    ``finish`` translates this into the ``answer``, the register projection and the ``final``
    stage row; it decides nothing, so no branch of the derivation is reachable only by building
    a turn.

    ``rail_status`` is carried rather than derived at the boundary, for the reason
    :class:`~governed_bi.serve.abstention.AbstentionPatch` carries its own: a node's effect on the
    timeline is a claim of its own, and the adapter computing it would mean the boundary, not the
    decision, deciding what the timeline says.
    """

    #: The register's ``outcome`` field.
    outcome: Outcome
    #: Who refused, in the shared vocabulary. ``None`` on every path that did not refuse.
    refused_by: str | None = None
    #: The stage that raised, on a crash. ``None`` everywhere else.
    failed_stage: str | None = None
    #: The exception class, on a crash. ``None`` everywhere else.
    error_type: str | None = None
    #: **System** copy for the answer card — the guard's public message on a refusal, ``None``
    #: otherwise. Distinct from the model's ``answer_text``, which ``finish`` copies off state.
    text: str | None = None
    #: The ledger as the record should carry it, which is not always the ledger as it arrived:
    #: ``finish``'s ``classify_turn`` rewrites ``terminal`` on a crash.
    execution: Mapping[str, Any] = field(default_factory=dict)
    #: Status for the turn's one ``final`` row.
    rail_status: str = "ok"
