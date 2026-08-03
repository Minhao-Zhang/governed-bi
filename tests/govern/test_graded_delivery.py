"""Graded delivery (ADR 0006 §5): the one layer a refusal may be forgiven at.

Its own file because it is its own concern and because it may not survive: ADR 0006
OQ4 asks whether the path earns its complexity at all, and with the rule narrowed to
one layer, deleting it is a small change. Keeping its tests together is what makes that
deletion a small change too.

The bypass it exists to prevent is B3, which is not a hypothetical: v1's attempt cap
wrote a ledger entry before ``check()`` ran, so the entry carried no layer; graded
delivery read ``failed_layer=None``, treated it as non-hard, and re-executed SQL that
had cleared nothing. Three attempts blocked at the column layer, the fourth capped,
card-number SQL would have reached the gateway.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("B")

CUSTOMERS = frozenset({"customers"})


@pytest.fixture
def layer():
    from governed_bi.govern.layers import Layer

    return Layer


# ── graded delivery (§5) ──────────────────────────────────────────────────────


def test_graded_delivery_forgives_the_cost_layer_and_nothing_else(check, layer) -> None:
    """The rule is one comparison against one member, and every other verdict —
    including a **passing** one and one with no layer at all — is not eligible.

    ADR 0006's first draft wrote ``{TABLES, COST}``, copied from v1's *entry* set; under
    that rule a pooled deployment would execute SQL against unlicensed tables and show
    the analyst the rows.
    """
    from governed_bi.govern.check import graded_delivery_eligible
    from governed_bi.govern.policy import GovernancePolicy

    budgeted = GovernancePolicy(cost_budget=0)
    cost = check(
        "SELECT c.id FROM customers c",
        licensed=CUSTOMERS,
        allowed_columns=frozenset({"customers.id"}),
        policy=budgeted,
    )
    assert cost["failed_layer"] is layer.COST
    assert graded_delivery_eligible(cost, budgeted) is True

    unlicensed = check("SELECT c.id FROM secrets c", licensed=CUSTOMERS,
                       allowed_columns=frozenset({"secrets.id"}))
    assert unlicensed["failed_layer"] is layer.TABLES
    assert graded_delivery_eligible(unlicensed) is False

    passing = check("SELECT c.id FROM customers c", licensed=CUSTOMERS,
                    allowed_columns=frozenset({"customers.id"}))
    assert graded_delivery_eligible(passing) is False, "failed_layer=None never means eligible"

    assert graded_delivery_eligible(cost, GovernancePolicy(cost_budget=0,
                                                           graded_delivery_enabled=False)) is False


def test_the_cost_layer_does_not_run_when_it_has_no_budget(check, layer) -> None:
    """``cost_budget`` ships ``UNSET`` — the layer is absent from ``layers_evaluated``
    rather than passing, so "disabled" and "ran and approved" stay distinguishable."""
    from governed_bi.govern.policy import GovernancePolicy

    default = check(
        "SELECT c.id FROM customers c JOIN orders o ON o.cid = c.id",
        licensed=frozenset({"customers", "orders"}),
        allowed_columns=frozenset({"customers.id", "orders.cid"}),
    )
    assert default["passed"] is True
    assert layer.COST not in default["layers_evaluated"]

    with_budget = check(
        "SELECT c.id FROM customers c JOIN orders o ON o.cid = c.id",
        licensed=frozenset({"customers", "orders"}),
        allowed_columns=frozenset({"customers.id", "orders.cid"}),
        policy=GovernancePolicy(cost_budget=99),
    )
    assert layer.COST in with_budget["layers_evaluated"]
