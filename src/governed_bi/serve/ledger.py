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

__all__ = ["attempt_field", "execution_from_attempts", "cap_attempt"]


def attempt_field(attempt: Any, name: str) -> Any:
    """One field of a ledger row, whether it arrived as a mapping or an object."""
    if isinstance(attempt, Mapping):
        return attempt.get(name)
    return getattr(attempt, name, None)


def execution_from_attempts(attempts: Sequence[Any]) -> dict[str, Any]:
    """The turn's :class:`ExecutionRecord`, with ``terminal`` read off the **ledger**.

    Not from whether a SQL string exists. ``has_sql`` came from the tool-call
    *arguments*, so producing a string counted as producing an answer: a turn whose
    every attempt was refused recorded ``terminal: "answered"`` beside
    ``passed: false``, which is the crash-counted-as-refusal inversion that retired the
    pre-2026-07-25 numbers, pointing the other way.

    The vocabulary is ``govern.ledger.ExecutionRecord``'s. ``"graded"`` belongs to the
    graded-delivery path and is not written here.
    """
    rows = list(attempts)
    if not rows:
        return execution_record(rows, "no_sql")
    if any(attempt_field(a, "passed") is True for a in rows):
        return execution_record(rows, "answered")
    if any(attempt_field(a, "reason_code") == ATTEMPT_CAP_REFUSED_BY for a in rows):
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
