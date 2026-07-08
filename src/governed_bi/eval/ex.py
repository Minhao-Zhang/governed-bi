"""Execution accuracy (EX): the headline metric (D4).

The agent's result matches gold, verified by re-executing the gold SQL against
the same physical DB and comparing result sets. Automatable and trustworthy
because the dataset re-runs gold SQL. Cost/efficiency (wall-clock, tokens, rows;
BIRD's VES is reusable) are logged, not headline.

Comparison is set-based over row tuples (BIRD's official EX), so row order does
not matter but column order (per tuple) does. Any execution error on either side
counts as a non-match: the guardrails and gateway already ran, so a query that
still fails to execute did not produce the gold answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..gateway import Identity

if TYPE_CHECKING:
    from ..gateway import Gateway

# The eval runs read-only against BIRD with a single all-access identity (D7 dev
# profile); RLS is not part of accuracy scoring.
_EVAL_IDENTITY = Identity(user="eval", all_access=True)


def _result_set(sql: str, gateway: "Gateway") -> frozenset[tuple]:
    result = gateway.execute(sql, _EVAL_IDENTITY)
    return frozenset(tuple(row) for row in result.rows)


def execution_match(pred_sql: str, gold_sql: str, gateway: "Gateway") -> bool:
    """True if ``pred_sql`` and ``gold_sql`` produce the same result set."""
    if not pred_sql:
        return False
    try:
        return _result_set(pred_sql, gateway) == _result_set(gold_sql, gateway)
    except Exception:
        return False
