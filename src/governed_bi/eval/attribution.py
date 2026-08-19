"""Why a wrong answer was wrong, decided by parsing rather than by a model.

**Taken from RyanChenJung/governed-bi-utkuai@12c3e15, and the numbers in this file are theirs.**
Every figure below was measured on that fork's own runs; this tree has published no arm that
carries a ``failure_cause`` yet. They are kept rather than stripped because each one is the
argument for a specific check or a specific ordering, and a rule whose reason has been deleted is
a rule the next reader deletes.

The failure the instrument exists for: an arm could not tell whether its treatment helped,
because ``error_type`` was ``None`` on all 78 of its answered-but-wrong rows. The treatment was
aimed at decoy contact, which held 8 of those 78, and nothing said so until after the run. This
module is what makes a target sayable in advance.

**The order of the checks is the meaning of the output.** A row can satisfy several predicates at
once; the first that fires wins, and the ranking is by how much the cause explains. Decoy contact
leads because it is a semantic-layer failure -- the class a corpus-curation arm exists to fix --
and labelling such a row ``projection_extra`` because the decoy also widened the SELECT would hide
exactly the population such an arm needs to count.

**Nothing here reads or writes a grade.** ``attribute`` is a pure function of a row that is
already graded, and it returns ``None`` for any row that is not answered-and-wrong.

**Missing a prediction is not the same failure as an unparseable one.** All 8 of their baseline
rows that landed in ``unparseable`` had ``generated_sql: null`` and ``grade_detail:
"missing_prediction"`` -- the engine returned ``answered`` with no statement at all, never having
queried anything. That is a different failure from emitting a statement that will not parse;
exactly one row across both arms was the latter. Conflating them reports "answered nothing" and
"answered garbage" under one name and buries the more interesting of the two.

**This classifier has already decided one conclusion, and got it wrong once.** The fork published,
then retracted, "``table_set_differs`` is the largest bucket at 23 of 78, so build a curation
arm". The cause was ``_table_names`` counting CTE names as base tables (see its docstring): 9 rows
were mislabelled, and with them corrected the distribution is ``projection_extra`` 26,
``unattributed`` 18, ``table_set_differs`` 14, ``missing_prediction`` 8, ``decoy_contact`` 8,
``filter_differs`` 2, ``projection_missing`` 2 -- which licenses a result-shape check rather than a
curation arm. Two things follow for anyone changing the checks below.

*A ranking needs a test per ordering decision, not per category.* Every test they had asserted
that some row lands in some bucket, and all of them passed while the largest bucket was wrong by 9
rows. Nothing compared two candidate causes on one row, which is the only kind of check a
first-match-wins ranking can be wrong about.

*None of the run's consistency checks could see it.* ``decoy_contact == 8`` and the 78-row total
are both insensitive to which wrong-answer bucket a row lands in, and the residual judge only ever
read ``unattributed``. A classifier whose output is a ranking has to be audited against the parse,
not against its own totals.
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
    #: Returns more output columns than gold. The most-reported defect across the fork's own
    #: rounds and, once CTE names stopped inflating ``table_set_differs``, also the largest
    #: measured one: 26 of their 78 wrong rows.
    projection_extra = "projection_extra"
    #: Returns fewer output columns than gold.
    projection_missing = "projection_missing"
    #: Aggregate functions present in one and not the other.
    aggregation_differs = "aggregation_differs"
    #: Filters on a different set of columns than gold.
    filter_differs = "filter_differs"
    #: Shapes agree and the result still differs. The residual: a judge reads this bucket.
    unattributed = "unattributed"


def attribute(row: Mapping[str, object]) -> FailureCause | None:
    """The cause of ``row``'s failure, or ``None`` if ``row`` is not a wrong answer.

    ``correct`` is three-valued and this reads all three. ``grade_turn`` returns ``None`` for a
    row with no gold -- the instrument had nothing to compare against -- and ``projection.py``
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
    """Base table names, lowercased and unqualified, with CTE names removed.

    Unqualified because gold and prediction disagree on schema prefixes routinely and that
    disagreement is not a failure -- ``beer_factory.wurzelbier`` and ``wurzelbier`` are the
    same table, and counting them as ``table_set_differs`` would swallow the bucket.

    **A CTE reference is an ``exp.Table`` in sqlglot's AST.** ``WITH ranked AS (...) SELECT ...
    FROM ranked`` parses the ``ranked`` in the FROM clause as ``exp.Table``, indistinguishable
    by class from a real one; the CTE *definition* is a separate ``exp.CTE`` node whose
    ``alias_or_name`` is that same name. So ``find_all(exp.Table)`` collects a name that is not
    a table at all, and a prediction that restructures gold's query into a CTE reads as
    touching a table gold does not have.

    Subtracting the ``exp.CTE`` alias set is therefore not redundant with the unqualifying
    above -- these are two different filters that happen both to be about names, and a reader
    who removes this one as a duplicate re-opens the defect. Measured on the fork's own
    artifacts: 9 of 23 ``table_set_differs`` rows had base-table sets identical to gold, which
    is enough to make ``projection_extra`` rather than ``table_set_differs`` the largest bucket
    and to change which arm the run licensed.
    """
    return frozenset(
        t.name.lower() for t in tree.find_all(exp.Table) if t.name
    ) - {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}


def _arity(tree: exp.Expression) -> int:
    """Output column count of the outermost SELECT.

    ``SELECT *`` is counted as ``-1`` so it never compares equal to an explicit list: a star
    against three named columns is a real difference and reporting it as equal arity would
    push the row into ``unattributed``.

    **``WITH`` does not fool this the way it fools ``_table_names``.** sqlglot hangs the CTE list
    off the outer ``Select`` as its ``with`` argument rather than wrapping it, so the parsed root
    of ``WITH ranked AS (...) SELECT id, n FROM ranked`` *is* that outer ``Select`` and
    ``find(exp.Select)`` returns it, not the CTE's inner one. Checked: the query above reports
    arity 2, gold's projection, and not the CTE body's. Recorded because the natural fear after
    the CTE defect below is that every ``find``/``find_all`` here shares it, and this one does
    not.
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
