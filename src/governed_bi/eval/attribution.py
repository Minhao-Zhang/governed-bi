"""Why a wrong answer was wrong, decided by parsing rather than by a model.

Experiment 008 could not tell whether the Setup Wizard helped, because ``error_type`` was
``None`` on all 78 of its answered-but-wrong rows. A treatment was aimed at decoy contact,
which held 8 of those 78, and nothing said so until after the run. This module is the
instrument that makes a target sayable in advance.

**The order of the checks is the meaning of the output.** A row can satisfy several
predicates at once; the first that fires wins, and the ranking is by how much the cause
explains. Decoy contact leads because it is the semantic-layer failure class ``curator/``
exists to fix -- labelling such a row ``projection_extra`` because the decoy also widened
the SELECT would hide exactly the population a curation arm needs to count.

**Nothing here reads or writes a grade.** ``attribute`` is a pure function of a row that is
already graded, and it returns ``None`` for any row that is not answered-and-wrong.

**Missing a prediction is not the same failure as an unparseable one.** All 8 baseline rows
that landed in ``unparseable`` had ``generated_sql: null`` and ``grade_detail:
"missing_prediction"`` -- the engine returned ``answered`` with no statement at all, never
having queried anything. That is a different failure from emitting a statement that will not
parse; exactly one row across both arms was the latter. Conflating them would report "answered
nothing" and "answered garbage" under the same name and bury the more interesting of the two.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

import sqlglot
from sqlglot import exp

from governed_bi.govern.scopes import iter_scopes

__all__ = ["FailureCause", "attribute"]


class FailureCause(str, Enum):
    """Named causes, ordered as the checks run in :func:`attribute`."""

    #: Touched a BIRD-Obfuscation decoy column or table. The semantic-layer class.
    decoy_contact = "decoy_contact"
    #: No statement at all -- ``answered`` with nothing to parse. Ranked before
    #: ``unparseable`` so a row with no SQL never reaches ``sqlglot.parse_one``.
    missing_prediction = "missing_prediction"
    #: The statement does not parse. A cause, not a residual.
    unparseable = "unparseable"
    #: Reads a different set of base tables than gold.
    table_set_differs = "table_set_differs"
    #: Returns more output columns than gold. The most-reported defect in this line.
    projection_extra = "projection_extra"
    #: Returns fewer output columns than gold.
    projection_missing = "projection_missing"
    #: Aggregate functions present in one and not the other.
    aggregation_differs = "aggregation_differs"
    #: Filters on a different set of columns than gold.
    filter_differs = "filter_differs"
    #: Shapes agree and the result still differs. Task 3's judge reads this bucket.
    unattributed = "unattributed"


def attribute(row: Mapping[str, object]) -> FailureCause | None:
    """The cause of ``row``'s failure, or ``None`` if ``row`` is not a wrong answer.

    ``correct`` is three-valued and this reads all three. ``grade_turn`` returns ``None`` for a
    row with no gold -- the instrument had nothing to compare against -- and ``harness.py``
    propagates it rather than coercing, with a comment saying that ``bool(grade["correct"])``
    here would turn every ``missing_gold`` into a wrong answer. Testing ``is not False`` is what
    honours that: a truthiness test also caught ``None`` and classified the row, so a question
    with no answer key was published as an engine defect -- ``error_type: "unparseable"``,
    because an empty ``gold_sql`` does not parse. ``None`` is not wrong.
    """
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


def _table_names(tree: exp.Expression) -> frozenset[str]:
    """Base table names, lowercased and unqualified.

    Unqualified because gold and prediction disagree on schema prefixes routinely and that
    disagreement is not a failure -- ``beer_factory.wurzelbier`` and ``wurzelbier`` are the
    same table, and counting them as ``table_set_differs`` would swallow the bucket.
    """
    return frozenset(
        t.name.lower() for t in tree.find_all(exp.Table) if t.name
    )


def _arity(tree: exp.Expression) -> int:
    """Output column count of the outermost SELECT.

    ``SELECT *`` is counted as ``-1`` so it never compares equal to an explicit list: a star
    against three named columns is a real difference and reporting it as equal arity would
    push the row into ``unattributed``.
    """
    select = tree.find(exp.Select)
    if select is None:
        return 0
    projections = list(select.expressions)
    if any(isinstance(p, exp.Star) for p in projections):
        return -1
    return len(projections)


def _has_aggregate(tree: exp.Expression) -> bool:
    return any(isinstance(node, exp.AggFunc) for node in tree.walk())


def _filter_columns(tree: exp.Expression) -> frozenset[str]:
    """Columns named inside WHERE and HAVING, across every scope.

    Uses ``govern/scopes.py``'s walker rather than a bare ``find_all`` so a correlated
    subquery's predicate is attributed to the statement that contains it -- the same reason
    the governance layers walk scopes instead of the raw tree.
    """
    names: set[str] = set()
    for scope in iter_scopes(tree):
        for clause in (exp.Where, exp.Having):
            for node in scope.expression.find_all(clause):
                names.update(c.name.lower() for c in node.find_all(exp.Column) if c.name)
    return frozenset(names)
