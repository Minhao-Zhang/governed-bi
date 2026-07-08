"""Tests for the SQL guardrail stack (gateway/guardrails.py).

L1 (syntax) and L2 (policy blacklist) are enforced today; later milestones add
L3 to L5. These tests assert only what is currently enforced.
"""

from __future__ import annotations

import pytest

from governed_bi.gateway import GuardrailLayer, check

# L3 is not enforced yet, so the allowlist is irrelevant to these cases; pass an
# empty set and let L1/L2 do the work.
NO_COLUMNS: set[str] = set()


def _check(sql: str):
    return check(sql, allowed_columns=NO_COLUMNS, hard_block_suspect=True, dialect="sqlite")


# --------------------------------------------------------------------------- #
# L1: syntax
# --------------------------------------------------------------------------- #


def test_valid_select_passes():
    verdict = _check("SELECT CustomerID FROM customers WHERE amount > 1")
    assert verdict.passed
    assert verdict.failed_layer is None


def test_unparseable_sql_fails_syntax():
    verdict = _check("SELECT FROM WHERE ((")
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.syntax


def test_empty_sql_fails_syntax():
    verdict = _check("   ")
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.syntax


# --------------------------------------------------------------------------- #
# L2: policy blacklist (read-only single statement)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO customers VALUES ('x')",
        "UPDATE customers SET name = 'x'",
        "DELETE FROM customers",
        "DROP TABLE customers",
        "CREATE TABLE t (a INT)",
        "ALTER TABLE customers ADD COLUMN x INT",
        "PRAGMA table_info(customers)",
        "VACUUM",
        "SELECT * INTO backup FROM customers",
    ],
)
def test_non_readonly_statements_fail_policy(sql):
    verdict = _check(sql)
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.policy_blacklist


def test_multiple_statements_fail_policy():
    verdict = _check("SELECT 1; DROP TABLE customers")
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.policy_blacklist


def test_stacked_write_in_second_statement_is_blocked():
    # A benign-looking first statement must not smuggle a write past the gate.
    verdict = _check("SELECT CustomerID FROM customers; DELETE FROM customers")
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.policy_blacklist


def test_cte_select_passes():
    sql = "WITH recent AS (SELECT CustomerID FROM orders) SELECT CustomerID FROM recent"
    assert _check(sql).passed


def test_union_select_passes():
    assert _check("SELECT CustomerID FROM customers UNION SELECT CustomerID FROM orders").passed


def test_subquery_select_passes():
    sql = "SELECT name FROM customers WHERE CustomerID IN (SELECT CustomerID FROM orders)"
    assert _check(sql).passed
