"""Tests for the SQL guardrail stack (gateway/guardrails.py).

L1 (syntax), L2 (policy blacklist), and L3 (AST column allowlist) are enforced;
L4 to L5 land later. The allowlist is built from the committed beer_factory
corpus, so no live database is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.corpus import load_corpus
from governed_bi.gateway import GuardrailLayer, check, column_allowlist

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
ALLOWLIST = column_allowlist(load_corpus(CORPUS_ROOT, db="beer_factory").for_server())


def _check(sql: str, *, hard_block_suspect: bool = True):
    return check(
        sql,
        allowed_columns=set(ALLOWLIST.allowed),
        suspect_columns=ALLOWLIST.suspect,
        hard_block_suspect=hard_block_suspect,
        dialect="sqlite",
    )


# --------------------------------------------------------------------------- #
# L1: syntax
# --------------------------------------------------------------------------- #


def test_valid_select_passes():
    verdict = _check("SELECT c.CustomerID, c.First FROM customers c WHERE c.State = 'IL'")
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
        "UPDATE customers SET First = 'x'",
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
    verdict = _check("SELECT c.CustomerID FROM customers c; DELETE FROM customers")
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.policy_blacklist


def test_cte_select_passes():
    sql = (
        'WITH recent AS (SELECT CustomerID FROM "transaction") '
        "SELECT CustomerID FROM recent"
    )
    assert _check(sql).passed


def test_union_select_passes():
    assert _check('SELECT CustomerID FROM customers UNION SELECT CustomerID FROM "transaction"').passed


def test_subquery_select_passes():
    sql = 'SELECT First FROM customers WHERE CustomerID IN (SELECT CustomerID FROM "transaction")'
    assert _check(sql).passed


# --------------------------------------------------------------------------- #
# L3: AST column allowlist
# --------------------------------------------------------------------------- #


def test_allowlist_shape():
    # The PII column ships governance.excluded, so it is in neither set.
    assert "customers.ZipCode" in ALLOWLIST.suspect
    assert all("CreditCardNumber" not in ref for ref in ALLOWLIST.allowed | ALLOWLIST.suspect)


def test_unknown_column_is_blocked():
    verdict = _check("SELECT c.Nonexistent FROM customers c")
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.ast_column_allowlist


def test_unknown_source_is_blocked():
    verdict = _check("SELECT x.CustomerID FROM customers c")
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.ast_column_allowlist


def test_excluded_column_is_blocked():
    verdict = _check('SELECT t.CreditCardNumber FROM "transaction" t')
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.ast_column_allowlist


def test_alias_and_real_name_both_resolve():
    # A column may reference the table by alias or by its real name.
    assert _check("SELECT c.First FROM customers c").passed
    assert _check("SELECT customers.First FROM customers c").passed


def test_derived_cte_column_is_deferred_not_blocked():
    # SUM(PurchasePrice) reads from the CTE projection; the base reference inside
    # the CTE is what gets checked.
    sql = (
        'WITH r AS (SELECT PurchasePrice FROM "transaction") '
        "SELECT SUM(PurchasePrice) AS total FROM r"
    )
    assert _check(sql).passed


# --------------------------------------------------------------------------- #
# L3: suspect-column enforcement toggle (Server "three points" #1)
# --------------------------------------------------------------------------- #


def test_suspect_column_hard_blocked_in_dev():
    verdict = _check("SELECT c.ZipCode FROM customers c", hard_block_suspect=True)
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.ast_column_allowlist
    assert "suspect" in (verdict.reason or "")


def test_suspect_column_allowed_in_prod():
    verdict = _check("SELECT c.ZipCode FROM customers c", hard_block_suspect=False)
    assert verdict.passed


def test_suspect_bare_column_hard_blocked_in_dev():
    verdict = _check('SELECT ZipCode FROM customers', hard_block_suspect=True)
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.ast_column_allowlist


# --------------------------------------------------------------------------- #
# L5: cross-join / cartesian-product cost guard
# --------------------------------------------------------------------------- #


def test_comma_join_without_predicate_is_blocked():
    # Two base tables, no equality linking them -> unconstrained cross join.
    verdict = _check('SELECT c.CustomerID, t.PurchasePrice FROM customers c, "transaction" t')
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.cost_estimate


def test_explicit_join_on_passes():
    sql = (
        'SELECT c.CustomerID, t.PurchasePrice '
        'FROM customers c JOIN "transaction" t ON c.CustomerID = t.CustomerID'
    )
    assert _check(sql).passed


def test_comma_join_linked_in_where_passes():
    sql = (
        'SELECT c.CustomerID, t.PurchasePrice '
        'FROM customers c, "transaction" t WHERE c.CustomerID = t.CustomerID'
    )
    assert _check(sql).passed


def test_single_table_select_passes_cost_guard():
    assert _check("SELECT c.CustomerID FROM customers c").passed


# --------------------------------------------------------------------------- #
# L4: term-semantics (only enforced when allowed_tables is supplied)
# --------------------------------------------------------------------------- #


def _check_scoped(sql: str, allowed_tables):
    return check(
        sql,
        allowed_columns=set(ALLOWLIST.allowed),
        suspect_columns=ALLOWLIST.suspect,
        allowed_tables=frozenset(allowed_tables),
        hard_block_suspect=True,
        dialect="sqlite",
    )


def test_term_semantics_allows_in_scope_table():
    assert _check_scoped("SELECT c.First FROM customers c", {"customers"}).passed


def test_term_semantics_blocks_out_of_scope_table():
    verdict = _check_scoped("SELECT c.First FROM customers c", {"transaction"})
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.term_semantics


def test_term_semantics_skipped_when_scope_is_none():
    # Default _check passes allowed_tables=None, so L4 does not run.
    assert _check("SELECT c.First FROM customers c").passed


# --------------------------------------------------------------------------- #
# Regression: adversarial-review findings
# --------------------------------------------------------------------------- #


def test_star_projection_is_blocked():
    # SELECT * cannot be checked against the allowlist, so it is blocked.
    verdict = _check("SELECT * FROM customers")
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.ast_column_allowlist


def test_qualified_star_projection_is_blocked():
    verdict = _check("SELECT c.* FROM customers c")
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.ast_column_allowlist


def test_count_star_is_allowed():
    # COUNT(*) is an aggregate, not a star projection; it must not be blocked.
    assert _check("SELECT COUNT(*) AS n FROM customers").passed


def test_bare_excluded_column_via_inert_cte_is_blocked():
    # A no-op CTE must not flip a query-global flag that defers the excluded
    # (PII) column; scope-aware L3 blocks the bare reference.
    sql = 'WITH d AS (SELECT 1 AS x) SELECT CreditCardNumber FROM "transaction"'
    verdict = _check(sql)
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.ast_column_allowlist


def test_bare_unknown_column_via_sibling_subquery_is_blocked():
    sql = "SELECT Nonexistent FROM customers, (SELECT 1 AS x) sub"
    verdict = _check(sql)
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.ast_column_allowlist


def test_bare_projected_derived_column_is_allowed():
    # A bare column projected from a CTE (no base table in that scope) is fine.
    sql = 'WITH r AS (SELECT PurchasePrice FROM "transaction") SELECT PurchasePrice FROM r'
    assert _check(sql).passed


def test_tokenizer_error_fails_syntax_not_crash():
    # An unterminated literal raises sqlglot TokenError; L1 must catch it.
    verdict = _check('SELECT "unterminated')
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.syntax
