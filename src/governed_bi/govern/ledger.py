"""Audit ledger. See ADR 0006 and ADR 0010. Hash the executed string. Do not add a second, redacted projection:
:func:`attempt_record` stores ``executed_sql`` raw and ``checked_sql`` as the text that cleared the checks."""


from __future__ import annotations

import hashlib
from typing import Iterable, Literal, Sequence, TypedDict, get_args, get_type_hints

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

from ..registry.stages import Outcome
from .layers import GUARDRAIL_ERROR, CheckResult
from .policy import DEFAULT_DIALECT

__all__ = [
    "ExecutorPath",
    "EXECUTOR_PATHS",
    "AttemptRecord",
    "ExecutionRecord",
    "statement_sha256",
    "structural_fingerprint",
    "attempt_record",
    "guardrail_errors",
    "execution_record",
]

#: The four executors. See ADR 0006.
ExecutorPath = Literal["agent", "graded", "sample", "profile"]

#: The same four as data, so a caller does not re-list them.
EXECUTOR_PATHS: tuple[ExecutorPath, ...] = ("agent", "graded", "sample", "profile")


class AttemptRecord(TypedDict):
    """``result_layer`` is the layer name, not the enum: a checkpoint round-trips through JSON. See ADR 0006."""

    result_layer: str | None
    passed: bool
    reason_code: str
    path: ExecutorPath
    #: Statement sent after canonicalisation and the row limit; ``None`` when nothing ran.
    executed_sql: str | None
    #: Canonical text that cleared the checks, before the row limit; ``None`` when nothing ran.
    checked_sql: str | None


class ExecutionRecord(TypedDict):
    """Written every turn, including turns with no SQL."""

    attempts: list[AttemptRecord]
    #: ``crashed`` only when the turn failed after attempts existed.
    #: ``no_sql`` matches ``Outcome.no_sql``. ``finish`` classifies from this field, so a
    #: second spelling records a statement-less turn as ``crashed``.
    terminal: Literal["answered", "graded", "refused", "capped", "no_sql", "crashed"]
    #: Exceptions swallowed by ``check()``. Zero is a quotability precondition.
    guardrail_errors: int


def statement_sha256(sql: str) -> str:
    """Digest of the exact string sent to the database, not of the string that was checked."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def structural_fingerprint(sql: str, *, dialect: str = DEFAULT_DIALECT) -> str:
    """Literals elided. ``"unparseable"`` rather than raising, so a rejected statement still has a record."""
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except SqlglotError:
        return "unparseable"
    if tree is None:
        return "empty"
    elided = tree.copy()
    for node in list(elided.find_all(exp.Literal)):
        node.replace(exp.Literal.string("?") if node.is_string else exp.Literal.number(0))
    return elided.sql(dialect=dialect)


def attempt_record(
    result: CheckResult,
    path: ExecutorPath,
    *,
    executed_sql: str | None = None,
    checked_sql: str | None = None,
) -> AttemptRecord:
    """``executed_sql`` is what ``prepare`` sent, limit included. ``checked_sql`` is the text that cleared the checks.

    ``None`` means nothing ran. A refusal keeps no checked text, even when canonicalisation produced one.
    """
    # The row limit is applied only after the checks pass, so a statement that never ran has neither string.
    if executed_sql is None:
        checked_sql = None
    failed = result["failed_layer"]
    return AttemptRecord(
        result_layer=failed.name if failed is not None else None,
        passed=result["passed"],
        reason_code=result["reason_code"],
        path=path,
        executed_sql=executed_sql,
        checked_sql=checked_sql,
    )


def guardrail_errors(attempts: Iterable[AttemptRecord]) -> int:
    """Derived from the attempts. A second counter can disagree, and that disagreement is a run that looks clean."""
    return sum(1 for attempt in attempts if attempt["reason_code"] == GUARDRAIL_ERROR)


def execution_record(
    attempts: Sequence[AttemptRecord],
    terminal: Literal["answered", "graded", "refused", "capped", "no_sql", "crashed"],
) -> ExecutionRecord:
    """Assemble the turn's record. ``guardrail_errors`` is derived, never passed in."""
    return ExecutionRecord(
        attempts=list(attempts),
        terminal=terminal,
        guardrail_errors=guardrail_errors(attempts),
    )


def _assert_the_no_statement_word_is_shared() -> None:
    """Must equal ``Outcome.no_sql``. A drift makes ``finish`` record a statement-less turn as ``crashed``."""
    # ``get_type_hints``, not ``__annotations__``: postponed annotations store strings, so
    # ``get_args`` would return ``()`` and this guard would pass on every spelling.
    vocabulary = set(get_args(get_type_hints(ExecutionRecord)["terminal"]))
    if Outcome.no_sql.value not in vocabulary:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"Outcome.no_sql is {Outcome.no_sql.value!r} and ExecutionRecord.terminal declares "
            f"{sorted(vocabulary)}. finish reads terminal to classify that outcome, so the two "
            "must be one word."
        )


_assert_the_no_statement_word_is_shared()
