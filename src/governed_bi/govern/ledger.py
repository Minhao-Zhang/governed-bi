"""Audit ledger (ADR 0006 §11) and what measurement must see (§12).

Every executor writes an entry stamped with its ``path`` (G2).

Invariants: hash the executed string, not the checked one (G4);
:func:`guardrail_errors` is derived from attempts (quotability precondition).

ADR 0006 §11's redacted retention table (``ledger_entry()``) is gone — §11's own superseding
note, 2026-08-06, and ADR 0010's resolution paragraph record it:
it had zero production callers while :func:`attempt_record` was what reached disk,
carrying ``executed_sql`` raw. Do not re-add a redacted projection with no writer — it
is a second answer to "what is the durable record", and the one a reader believes.
"""


from __future__ import annotations

import hashlib
from typing import Iterable, Literal, Sequence, TypedDict, get_args, get_type_hints

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

from ..register.stages import Outcome
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

    ``verdict_layer`` is the layer's **name**, not the :class:`~.layers.Layer`, because a
    checkpoint round-trips through JSON. ``eval/projection.py::_attempt_trace`` re-keys it to
    ``layer`` for the measurement row, so the two spellings are one field.
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
    #: ``crashed`` is written only by ``stamp`` when the turn failed after attempts
    #: existed — so ``outcome=crashed`` never sits beside ``terminal=answered``.
    #:
    #: ``"no_sql"`` is also an :class:`~governed_bi.register.stages.Outcome` member, and
    #: :func:`_assert_the_no_statement_word_is_shared` asserts the two spellings at import.
    #: ``stamp`` classifies that outcome by reading *this* field, so a drift here would leave
    #: ``outcome`` and ``terminal`` answering "did a statement run" differently.
    terminal: Literal["answered", "graded", "refused", "capped", "no_sql", "crashed"]
    #: Exceptions swallowed by ``check()``. ``== 0`` joins the quotability
    #: preconditions.
    guardrail_errors: int


def statement_sha256(sql: str) -> str:
    """Digest of the exact string sent to the database."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def structural_fingerprint(sql: str, *, dialect: str = DEFAULT_DIALECT) -> str:
    """The statement's shape, with every literal elided.

    Returns ``"unparseable"`` rather than raising: the ledger must be able to describe
    a statement the parse layer rejected, which is the record that matters most.
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

    ``executed_sql`` is :func:`~governed_bi.govern.pipeline.prepare`'s output, so the row
    says what the engine sent, not what the model asked for. ``None`` means a refused
    attempt sent nothing — a value, not a gap.
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

    Derived rather than counted alongside: a separate counter is a second table that
    must agree, and the disagreeing case is the one that makes a run look clean.
    """
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
    """Import-time: the ledger's "no statement ran" word is ``Outcome.no_sql``'s.

    Asserted rather than imported, for :data:`~.layers.GUARDRAIL_REFUSED_BY`'s reason: the
    ``Literal`` above has to be spelled out to be a type at all. ``stamp`` derives
    ``Outcome.no_sql`` from this field, so if the two strings drift the outcome becomes
    unreachable and every statement-less turn silently records ``crashed`` instead.
    """
    # ``get_type_hints``, not ``__annotations__``: ``from __future__ import annotations`` makes
    # the latter a dict of strings, so ``get_args`` over it returns ``()`` and the guard passes
    # vacuously on every spelling.
    vocabulary = set(get_args(get_type_hints(ExecutionRecord)["terminal"]))
    if Outcome.no_sql.value not in vocabulary:  # pragma: no cover - import-time guard
        raise AssertionError(
            f"Outcome.no_sql is {Outcome.no_sql.value!r} and ExecutionRecord.terminal declares "
            f"{sorted(vocabulary)}. stamp reads terminal to classify that outcome, so the two "
            "must be one word."
        )


_assert_the_no_statement_word_is_shared()
