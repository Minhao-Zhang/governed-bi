"""Attempt rows -> the turn's ``ExecutionRecord``. One projection, three readers.

Lifted out of ``serve/tools.py`` so that ``stamp.py`` -- the recorder, which writes no tool and
calls no model -- does not import the tool surface. These functions are not about tools: they
are about what a sequence of governed attempts *means*, and ``eval/`` and the register read the
same vocabulary (``govern.ledger.ExecutionRecord``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from governed_bi.govern.ledger import AttemptRecord, execution_record
from governed_bi.register.stages import ATTEMPT_CAP_REFUSED_BY

__all__ = [
    "attempt_field",
    "INTROSPECTION_PATHS",
    "answering_attempts",
    "execution_from_attempts",
    "cap_attempt",
    "ledger_ended_without_answer",
]

#: Executor paths whose statements **introspect** rather than answer.
#:
#: ``sample`` runs a ``SELECT DISTINCT`` over one column; ``profile`` is declared for
#: distribution statistics. A turn in which only one of those succeeded has not answered.
#:
#: **Stated as the complement**, so a path added later counts as answering until someone says
#: otherwise: under-recording an answer is the failure mode this repository keeps producing, and
#: an allowlist would let a forgotten path silently drop out of the record.
#: ``tests/serve/test_state_channels.py`` closes the partition over ``EXECUTOR_PATHS``.
#:
#: ``guardrail_errors`` deliberately does **not** filter: a layer exception on any path is a
#: run-level fact, and hiding the sample path from that count is what made it vacuous.
INTROSPECTION_PATHS: frozenset[str] = frozenset({"sample", "profile"})


def attempt_field(attempt: Any, name: str) -> Any:
    """One field of a ledger row, whether it arrived as a mapping or an object."""
    if isinstance(attempt, Mapping):
        return attempt.get(name)
    return getattr(attempt, name, None)


def answering_attempts(attempts: Sequence[Any]) -> list[Any]:
    """The ledger rows from a path that can answer the question.

    One function, three readers (``execution_from_attempts``, ``stamp``'s outcome derivation,
    ``agent_core``'s ``generated_sql``): three copies of "which attempts count" is three
    answers, and the one that disagrees reports a turn as answered.
    """
    return [a for a in attempts if attempt_field(a, "path") not in INTROSPECTION_PATHS]


def execution_from_attempts(attempts: Sequence[Any]) -> dict[str, Any]:
    """The turn's :class:`ExecutionRecord`, with ``terminal`` read off the **ledger**.

    Not from whether a SQL string exists: ``has_sql`` came from the tool-call *arguments*, so a
    turn whose every attempt was refused recorded ``terminal: "answered"`` beside
    ``passed: false``.

    ``attempts`` keeps every row; ``terminal`` reads only :func:`answering_attempts`, so a turn
    that sampled a column and then answered from context is ``no_sql`` with a non-empty ledger.

    **The cap is tested before the pass, and the order is load-bearing.** Reversed, ``"capped"``
    is unreachable on any turn where a statement ever succeeded, and that turn exists: measured
    live, an agent with two passing and two blocked attempts hit the cap, told the user it could
    not answer, and was stamped ``terminal: "answered"``. ADR 0006 §5 — the cap **terminates the
    turn**, and an earlier success does not undo that.

    The vocabulary is ``govern.ledger.ExecutionRecord``'s. ``"graded"`` belongs to the
    graded-delivery path and is not written here.
    """
    rows = list(attempts)
    answering = answering_attempts(rows)
    if not answering:
        return execution_record(rows, "no_sql")
    if any(attempt_field(a, "reason_code") == ATTEMPT_CAP_REFUSED_BY for a in answering):
        return execution_record(rows, "capped")
    if any(attempt_field(a, "passed") is True for a in answering):
        return execution_record(rows, "answered")
    return execution_record(rows, "refused")


def ledger_ended_without_answer(state: Mapping[str, Any]) -> bool:
    """True when stamp will classify the turn capped/refused from ``execution.terminal``.

    ``agent_core`` still writes ``path_kind: answered`` on those turns (stamp's ledger branch
    requires it). Reflect/narrate use this so they do not decorate a refusal.
    """
    execution = state.get("execution")
    if not isinstance(execution, Mapping):
        return False
    return execution.get("terminal") in ("capped", "refused")


def cap_attempt() -> AttemptRecord:
    """The ledger row for a turn the attempt cap ended.

    Without it a capped turn carried an **empty** ledger, so "no attempt passed" held vacuously
    and ``ExecutionRecord`` declared ``"capped"`` with nothing ever writing it.

    Built directly rather than through :func:`~governed_bi.govern.layers.refuse`: the cap is not
    a layer verdict (ADR 0006 §5 keeps ``capped`` distinct from ``refused``), and a rule id would
    attribute it to a governance layer that never ran. The reason code is
    :data:`~governed_bi.register.stages.ATTEMPT_CAP_REFUSED_BY`, the declared value
    ``classify_outcome`` reads to return ``Outcome.capped``.
    """
    return AttemptRecord(
        verdict_layer=None,
        passed=False,
        reason_code=ATTEMPT_CAP_REFUSED_BY,
        path="agent",
        # Stated, not omitted: a ``TypedDict`` tolerates a missing key at runtime, so a row
        # built without it forces every consumer to ``.get()`` defensively.
        executed_sql=None,
    )
