"""Execution accuracy (EX) — the headline metric (D4).

The agent's result matches gold, verified by re-executing the gold SQL against
the same physical DB and comparing result sets. Automatable and trustworthy
because the dataset re-runs gold SQL. Cost/efficiency (wall-clock, tokens, rows;
BIRD's VES is reusable) are logged, not headline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..gateway import Gateway


def execution_match(pred_sql: str, gold_sql: str, gateway: "Gateway") -> bool:
    """True if ``pred_sql`` and ``gold_sql`` produce the same result set."""
    raise NotImplementedError("EX scoring pending; execute both and compare result sets")
