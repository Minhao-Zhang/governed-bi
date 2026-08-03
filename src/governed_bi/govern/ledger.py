"""The audit ledger (ADR 0006 §11) and what measurement must see (§12).

**Every executor writes an entry**, stamped with its ``path`` (G2). v1's
graded-delivery path bypassed the middleware entirely and produced answers whose
record showed a query that never happened.

**Retention is by vocabulary class, not by "drop every string".** ADR 0006's first
draft said the ledger "keeps numbers and drops every string" and four lines later
"keeps ``columns`` / ``row_count``" — column names are strings, and dropping every
string also drops the statement, in a section whose stated purpose is that the record
must show what ran. So:

===========================  ==================================================
field class                  durable projection
===========================  ==================================================
closed vocabulary            kept — ``layer``, ``passed``, ``reason_code``,
                             ``path``, ``rule_id``, ``outcome``
numbers                      kept — ``row_count``, ``truncated``, ``ms``,
                             ``attempt``
statement                    kept as ``sha256`` **plus a structural
                             fingerprint**: the parsed AST with every literal
                             elided
``detail``, driver errors,   dropped
prose, result rows
column names                 kept only as ids, never as free text
===========================  ==================================================

The fingerprint is what makes the record auditable — which tables, which shape,
which functions — without echoing literals. libpq embeds the offending statement in
its error text (``LINE 1: SELECT ...``), which is why free text goes.

**The hash is of the string that was executed**, not of the string that was checked
(G4). Three transformations act on a statement — normalisation, checking, row-limit
injection — and v1 hashed the wrong one, so the record attested to a statement the
database never saw.

**``guardrail_errors`` is a quotability precondition, not a diagnostic.** §12 records
the chain it exists to catch: a ``NameError`` in the function-layer walk turns every
turn in an arm into a refusal, ``crash_rate == 0``, every register key present, run
declared quotable. :func:`guardrail_errors` counts them from the attempts, so the
count and the attempts cannot disagree.
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Literal, Sequence, TypedDict

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

from .layers import GUARDRAIL_ERROR, CheckVerdict, Layer
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

#: The four executors ADR 0006 §7 enumerates. G2 is "every executor is enumerated,
#: passes ``check()``, and writes a ledger entry" — not "one choke point", which was
#: aspirational and which the ADR's own first draft contradicted in its own tool
#: table by listing ``sample_rows`` beside the "single" one.
ExecutorPath = Literal["agent", "graded", "sample", "profile"]

#: The same four, as data, so a caller can iterate them instead of re-listing them.
EXECUTOR_PATHS: tuple[ExecutorPath, ...] = ("agent", "graded", "sample", "profile")


class AttemptRecord(TypedDict):
    """One statement's trip through the stack. ADR 0006 §12."""

    verdict_layer: Layer | None
    passed: bool
    reason_code: str
    path: ExecutorPath


class ExecutionRecord(TypedDict):
    """Written **every turn**, including turns with no SQL at all.

    Total, and written unconditionally: a record that appears only when something
    happened cannot afterwards be told from instrumentation that was never wired up —
    half this repository's retired numbers have that shape.
    """

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


def attempt_record(verdict: CheckVerdict, path: ExecutorPath) -> AttemptRecord:
    """Project a verdict into the measurement layer's per-attempt row."""
    return AttemptRecord(
        verdict_layer=verdict["failed_layer"],
        passed=verdict["passed"],
        reason_code=verdict["reason_code"],
        path=path,
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
