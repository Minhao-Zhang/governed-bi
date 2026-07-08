"""Five-layer SQL guardrails (Server step 8; Architecture §6).

Ordered, fail-closed on any layer:

1. **syntax** — parses as valid SQL (sqlglot).
2. **policy blacklist** — no DDL/DML/PRAGMA/etc.; read-only only.
3. **AST column allowlist** — every referenced column is a known, non-excluded,
   non-``suspect`` column (dev/BIRD hard-blocks suspect; prod soft-warns).
4. **term-semantics** — referenced assets match the bound terms.
5. **cost / EXPLAIN** — estimated cost under budget.

These run in the server's ``wrap_tool_call`` middleware. The refuse-gate (D5)
runs *concurrently*, not as a sixth layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GuardrailLayer(str, Enum):
    syntax = "syntax"
    policy_blacklist = "policy_blacklist"
    ast_column_allowlist = "ast_column_allowlist"
    term_semantics = "term_semantics"
    cost_estimate = "cost_estimate"


@dataclass(frozen=True)
class GuardrailVerdict:
    passed: bool
    failed_layer: GuardrailLayer | None = None
    reason: str | None = None


def check(sql: str, *, allowed_columns: set[str], hard_block_suspect: bool) -> GuardrailVerdict:
    """Run the layers in order; return on the first failure (fail-closed)."""
    raise NotImplementedError("guardrail stack pending; layer 1 uses sqlglot for parsing")
