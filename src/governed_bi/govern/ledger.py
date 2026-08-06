"""Audit ledger (ADR 0006 §11) and what measurement must see (§12).

Every executor writes an entry stamped with its ``path`` (G2). Retention by
vocabulary class: closed vocabulary and numbers kept; statement kept as
``sha256`` plus a structural fingerprint (literals elided); ``detail``, driver
errors, prose, and result rows dropped; column names only as ids.

Invariants: hash the executed string, not the checked one (G4);
:func:`guardrail_errors` is derived from attempts (quotability precondition).
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
    "ledger_entry",
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


def ledger_entry(
    *,
    verdict: CheckVerdict,
    path: ExecutorPath,
    executed_sql: str | None,
    attempt: int,
    row_count: int | None = None,
    truncated: bool | None = None,
    ms: int | None = None,
    dialect: str = DEFAULT_DIALECT,
) -> dict[str, object]:
    """The durable projection of one governance decision.

    ``executed_sql`` is the string that reached the database, or ``None`` when the
    statement was blocked and nothing ran — and the entry says which, rather than
    leaving a reader to infer it from a missing key.

    ``detail`` from the verdict is **not** carried. It is the one field guaranteed to
    contain a fragment of the statement and, on a driver error, the statement itself.
    """
    failed_layer = verdict["failed_layer"]
    return {
        "path": path,
        "attempt": attempt,
        "passed": verdict["passed"],
        "layer": failed_layer.name if failed_layer is not None else None,
        "layer_value": int(failed_layer) if failed_layer is not None else None,
        "reason_code": verdict["reason_code"],
        "layers_evaluated": [layer.name for layer in verdict["layers_evaluated"]],
        "bound_references": sorted(verdict["bound"]),
        "executed": executed_sql is not None,
        "statement_sha256": statement_sha256(executed_sql) if executed_sql is not None else None,
        "statement_shape": (
            structural_fingerprint(executed_sql, dialect=dialect)
            if executed_sql is not None
            else None
        ),
        "row_count": row_count,
        "truncated": truncated,
        "ms": ms,
    }


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
