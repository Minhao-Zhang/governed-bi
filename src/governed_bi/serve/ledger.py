"""Attempt rows to the turn's ``ExecutionRecord``. One projection, three readers.

``finish`` writes no tool and calls no model, so it does not import the tool surface.
``eval`` and the register read the same ``ExecutionRecord`` vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from governed_bi.govern.layers import GUARDRAIL_ERROR
from governed_bi.govern.ledger import (
    AttemptRecord,
    ExecutionRecord,
    ExecutorPath,
    capped_error,
    execution_record,
)
from governed_bi.registry.stages import ATTEMPT_CAP_REFUSED_BY

__all__ = [
    "attempt_field",
    "INTROSPECTION_PATHS",
    "answering_attempts",
    "execution_from_attempts",
    "cap_attempt",
    "pipeline_error_attempt",
    "ledger_ended_without_answer",
]

#: Paths that introspect rather than answer. The complement of answering, so a new path
#: counts as answering until someone says otherwise. ``guardrail_errors`` does not filter:
#: a layer exception on any path is a run-level fact.
INTROSPECTION_PATHS: frozenset[str] = frozenset({"sample", "profile"})


def attempt_field(attempt: Any, name: str) -> Any:
    if isinstance(attempt, Mapping):
        return attempt.get(name)
    return getattr(attempt, name, None)


def answering_attempts(attempts: Sequence[Any]) -> list[Any]:
    """The rows from a path that can answer. One predicate, so the three readers agree.

    A second copy of "which attempts count" is how a turn gets reported as answered.
    """
    return [a for a in attempts if attempt_field(a, "path") not in INTROSPECTION_PATHS]


def execution_from_attempts(attempts: Sequence[Any]) -> ExecutionRecord:
    """``terminal`` from answering rows, not from a SQL string existing. See ADR 0006 §5.

    The cap is tested before a pass: reversed, ``capped`` is unreachable once any statement
    succeeded. Sampling then answering from context is ``no_sql`` with a non-empty ledger.
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
    """True when ``execution.terminal`` is capped or refused.

    ``agent_core`` still writes ``path_kind: answered`` on those turns, so reflect and
    narrate use this rather than ``path_kind`` and do not decorate a refusal.
    """
    execution = state.get("execution")
    if not isinstance(execution, Mapping):
        return False
    return execution.get("terminal") in ("capped", "refused")


def pipeline_error_attempt(
    path: ExecutorPath, detail: str, *, submitted_sql: str | None = None
) -> AttemptRecord:
    """Ledger row when the attempt died before a result. ``result_layer`` is ``None``.

    No row made ``finish`` read an empty ledger as answered from context. No layer ran, so a
    layer id here would attribute the failure to governance. ``check``'s own except names one.
    """
    return AttemptRecord(
        result_layer=None,
        passed=False,
        reason_code=GUARDRAIL_ERROR,
        path=path,
        executed_sql=None,
        checked_sql=None,
        submitted_sql=submitted_sql,
        error=capped_error(detail),
    )


def cap_attempt() -> AttemptRecord:
    """The row a capped turn needs, or an empty ledger makes ``capped`` hold vacuously.

    Not a layer result (ADR 0006 §5). A rule id would name a layer that never ran.
    Every SQL field is set to ``None``: a ``TypedDict`` tolerates a missing key.
    """
    return AttemptRecord(
        result_layer=None,
        passed=False,
        reason_code=ATTEMPT_CAP_REFUSED_BY,
        path="agent",
        executed_sql=None,
        checked_sql=None,
        submitted_sql=None,
        error=None,
    )
