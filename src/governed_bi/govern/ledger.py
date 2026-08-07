"""Audit ledger (ADR 0006 §11) and what measurement must see (§12).

Every executor writes an entry stamped with its ``path`` (G2).

Invariants: hash the executed string, not the checked one (G4);
:func:`guardrail_errors` is derived from attempts (quotability precondition).

**``ledger_entry()`` is gone** (audit §8.1 / §10). It was the only implementation of ADR
0006 §11's retention table -- ``executed``, ``statement_sha256``, ``statement_shape`` -- and
it had **zero production callers**: one re-export and four lines in a test file, with 45
green tests passing against dead code. What actually reached disk was
:func:`attempt_record`, carrying ``executed_sql`` raw.

Deleted with the rest of the redaction vocabulary rather than wired. The retention table it
implemented was a policy nothing enforced, and a redacted projection that no writer uses is
a second answer to "what is the durable record" -- the one a reader believes and the engine
never produced.

:func:`statement_sha256` and :func:`structural_fingerprint` stay. They have real callers
(the stream events include a statement digest) and they are useful facts about a statement
regardless of any retention policy.
"""


from __future__ import annotations

import hashlib
from typing import Iterable, Literal, Sequence, TypedDict

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

from .layers import GUARDRAIL_ERROR, CheckVerdict
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

#: The four executors ADR 0006 §7 enumerates (G2).
ExecutorPath = Literal["agent", "graded", "sample", "profile"]

#: The same four, as data, so a caller can iterate them instead of re-listing them.
EXECUTOR_PATHS: tuple[ExecutorPath, ...] = ("agent", "graded", "sample", "profile")


class AttemptRecord(TypedDict):
    """One statement's trip through the stack (ADR 0006 §12).

    ``verdict_layer`` is the layer's **name** (checkpoint-safe; matches ``trace()``).
    """

    verdict_layer: str | None
    passed: bool
    reason_code: str
    path: ExecutorPath
    #: Statement sent after canonicalisation and row limit; ``None`` when nothing ran.
    executed_sql: str | None


class ExecutionRecord(TypedDict):
    """Written every turn, including turns with no SQL."""

    attempts: list[AttemptRecord]
    terminal: Literal["answered", "graded", "refused", "capped", "no_sql"]
    #: Exceptions swallowed by ``check()``. ``== 0`` joins the quotability
    #: preconditions.
    guardrail_errors: int


def statement_sha256(sql: str) -> str:
    """Digest of the exact string sent to the database."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def structural_fingerprint(sql: str, *, dialect: str = DEFAULT_DIALECT) -> str:
    """The statement's shape, with every literal elided.

    Returns ``"unparseable"`` for a statement that does not parse — which is itself
    the fact worth recording, and is not an error condition here: the ledger must be
    able to describe a statement that the parse layer rejected, or the one record
    that matters most is the one that is missing.
    """
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
    verdict: CheckVerdict, path: ExecutorPath, *, executed_sql: str | None = None
) -> AttemptRecord:
    """Project a verdict into the measurement layer's per-attempt row.

    ``executed_sql`` is what :func:`~governed_bi.govern.pipeline.prepare` produced, so the
    row says what the engine sent rather than what the model asked for. It defaults to
    ``None`` because a refused attempt sent nothing, which is a value and not a gap.
    """
    failed = verdict["failed_layer"]
    return AttemptRecord(
        verdict_layer=failed.name if failed is not None else None,
        passed=verdict["passed"],
        reason_code=verdict["reason_code"],
        path=path,
        executed_sql=executed_sql,
    )


def guardrail_errors(attempts: Iterable[AttemptRecord]) -> int:
    """How many attempts died of an exception inside ``check()``.

    Derived from the attempts rather than counted alongside them: a separate counter
    is a second table that must agree, and the disagreeing case here is the one that
    makes a run look clean.
    """
    return sum(1 for attempt in attempts if attempt["reason_code"] == GUARDRAIL_ERROR)


def execution_record(
    attempts: Sequence[AttemptRecord],
    terminal: Literal["answered", "graded", "refused", "capped", "no_sql"],
) -> ExecutionRecord:
    """Assemble the turn's record. ``guardrail_errors`` is derived, never passed in."""
    return ExecutionRecord(
        attempts=list(attempts),
        terminal=terminal,
        guardrail_errors=guardrail_errors(attempts),
    )
