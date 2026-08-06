"""Attempt rows -> the turn's ``ExecutionRecord``. One projection, three readers.

**Lifted out of ``serve/tools.py``, and the import it removes is the reason.** ``stamp.py`` --
the recorder, the one node that writes no tool and calls no model -- imported the *tool module*
for two functions. A recorder reaching into the tool surface is a dependency nobody would
declare on purpose, and it existed only because these functions happened to be written where
their first caller was.

They are not about tools. They are about what a sequence of governed attempts *means*: whether
the turn answered, was refused, or was ended by the cap. ``eval/`` and the register read the
same vocabulary (``govern.ledger.ExecutionRecord``), so this is the projection between the
ledger and the record and belongs on its own.
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
]

#: Executor paths whose statements **introspect** rather than answer.
#:
#: ``EXECUTOR_PATHS`` has four members and they are not interchangeable. ``sample`` runs a
#: ``SELECT DISTINCT`` over one column to show the model what the values look like; ``profile``
#: is declared for distribution statistics. A turn in which only one of those succeeded has not
#: answered anything, and recording it as ``answered`` is the crash-counted-as-refusal inversion
#: arriving through a second executor path.
#:
#: **Stated as the complement**, so a path added later counts as answering until someone says
#: otherwise: under-recording an answer is the failure mode this repository keeps producing, and
#: an allowlist would make a forgotten path silently drop its statements out of the record.
#: ``tests/serve/test_state_channels.py`` closes the partition over ``EXECUTOR_PATHS`` so
#: "someone says otherwise" is a gate rather than a hope.
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

    One function, three readers (``execution_from_attempts``, ``stamp``'s outcome
    derivation, and ``agent_core``'s ``generated_sql``) — because three copies of "which
    attempts count" is three answers, and the one that disagrees is the one that reports a
    turn as answered.
    """
    return [a for a in attempts if attempt_field(a, "path") not in INTROSPECTION_PATHS]


def execution_from_attempts(attempts: Sequence[Any]) -> dict[str, Any]:
    """The turn's :class:`ExecutionRecord`, with ``terminal`` read off the **ledger**.

    Not from whether a SQL string exists. ``has_sql`` came from the tool-call
    *arguments*, so producing a string counted as producing an answer: a turn whose
    every attempt was refused recorded ``terminal: "answered"`` beside
    ``passed: false``, which is the crash-counted-as-refusal inversion that retired the
    pre-2026-07-25 numbers, pointing the other way.

    ``attempts`` keeps every row; ``terminal`` reads only :func:`answering_attempts`. A turn
    that sampled a column and then answered from context is ``no_sql`` with a non-empty
    ledger, and both halves of that are true.

    The vocabulary is ``govern.ledger.ExecutionRecord``'s. ``"graded"`` belongs to the
    graded-delivery path and is not written here.
    """
    rows = list(attempts)
    answering = answering_attempts(rows)
    if not answering:
        return execution_record(rows, "no_sql")
    if any(attempt_field(a, "passed") is True for a in answering):
        return execution_record(rows, "answered")
    if any(attempt_field(a, "reason_code") == ATTEMPT_CAP_REFUSED_BY for a in answering):
        return execution_record(rows, "capped")
    return execution_record(rows, "refused")


def cap_attempt() -> AttemptRecord:
    """The ledger row for a turn the attempt cap ended.

    ``_run_query`` returned on the cap *before* appending, so a capped turn carried an
    **empty** ledger while ``generated_sql`` was still read out of the tool arguments —
    and "no attempt passed" then held vacuously. ``ExecutionRecord`` declared
    ``"capped"`` and nothing ever wrote it.

    Built directly rather than through :func:`~governed_bi.govern.layers.refuse`: the cap
    is not a layer verdict (ADR 0006 §5 keeps ``capped`` distinct from ``refused``), and
    a rule id would attribute it to a governance layer that never ran. The reason code is
    :data:`~governed_bi.register.stages.ATTEMPT_CAP_REFUSED_BY`, which is the declared
    value ``classify_outcome`` reads to return ``Outcome.capped``.
    """
    return AttemptRecord(
        verdict_layer=None,
        passed=False,
        reason_code=ATTEMPT_CAP_REFUSED_BY,
        path="agent",
        # Stated, not omitted. A ``TypedDict`` tolerates a missing key at runtime, so a row
        # built without this one forces every consumer to ``.get()`` defensively -- and a
        # capped attempt sent nothing, which is a value.
        executed_sql=None,
    )
