"""Why a wrong answer was wrong, by parsing the statements.

The check order is the result: the first match wins. Decoy contact leads so a
decoy that also widened the SELECT is not labelled ``projection_extra``.
A missing statement and a statement that will not parse are different causes.
``correct is None`` means there was no gold; that row is not a wrong answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

import sqlglot
from sqlglot import exp

from governed_bi.govern.scopes import iter_scopes

__all__ = ["FailureCause", "attribute"]


class FailureCause(str, Enum):
    """Causes in check order. The first match wins."""

    decoy_contact = "decoy_contact"
    # Empty SQL is ranked above a parse failure so it never reaches parse_one.
    missing_prediction = "missing_prediction"
    unparseable = "unparseable"
    table_set_differs = "table_set_differs"
    projection_extra = "projection_extra"
    projection_missing = "projection_missing"
    aggregation_differs = "aggregation_differs"
    filter_differs = "filter_differs"
    unattributed = "unattributed"


def attribute(row: Mapping[str, object]) -> FailureCause | None:
    """``correct is not False`` keeps a missing gold out. A truthiness test published those rows as ``unparseable``."""
    if row.get("outcome") != "answered" or row.get("correct") is not False:
        return None

    if row.get("touched_decoy"):
        return FailureCause.decoy_contact

    pred_sql = str(row.get("generated_sql") or "")
    gold_sql = str(row.get("gold_sql") or "")
    if not pred_sql.strip():
        return FailureCause.missing_prediction
    try:
        pred = sqlglot.parse_one(pred_sql)
        gold = sqlglot.parse_one(gold_sql)
    except Exception:  # noqa: BLE001 — any parse failure is the same cause
        return FailureCause.unparseable
    if pred is None or gold is None:
        return FailureCause.unparseable

    if _table_names(pred) != _table_names(gold):
        return FailureCause.table_set_differs

    pred_arity, gold_arity = _arity(pred), _arity(gold)
    if pred_arity > gold_arity:
        return FailureCause.projection_extra
    if pred_arity < gold_arity:
        return FailureCause.projection_missing

    if _has_aggregate(pred) != _has_aggregate(gold):
        return FailureCause.aggregation_differs

    if _filter_columns(pred) != _filter_columns(gold):
        return FailureCause.filter_differs

    return FailureCause.unattributed


def _table_names(tree: exp.Expr) -> frozenset[str]:
    """Base tables, schema stripped, CTE names removed. A CTE reference is an ``exp.Table``; schema disagreement is not
    a table-set failure."""
    return frozenset(
        t.name.lower() for t in tree.find_all(exp.Table) if t.name
    ) - {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}


def _arity(tree: exp.Expr) -> int:
    """Outermost SELECT column count. ``SELECT *`` is ``-1``. ``WITH`` hangs on the outer ``Select``, not the CTE."""
    select = tree.find(exp.Select)
    if select is None:
        return 0
    projections = list(select.expressions)
    if any(isinstance(p, exp.Star) for p in projections):
        return -1
    return len(projections)


def _has_aggregate(tree: exp.Expr) -> bool:
    return any(isinstance(node, exp.AggFunc) for node in tree.walk())


def _filter_columns(tree: exp.Expr) -> frozenset[str]:
    """Columns in WHERE and HAVING, including nested queries. A scope walk keeps a correlated predicate; a bare
    ``find_all`` on one node does not."""
    names: set[str] = set()
    for scope in iter_scopes(tree):
        for clause in (exp.Where, exp.Having):
            for node in scope.expression.find_all(clause):
                names.update(c.name.lower() for c in node.find_all(exp.Column) if c.name)
    return frozenset(names)
