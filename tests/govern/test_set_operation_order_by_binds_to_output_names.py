"""``ORDER BY`` on a UNION references output names, and the binding layer must resolve them.

Postgres resolves a set operation's ``ORDER BY`` against the **output column list** — the names
of the leftmost branch — and nothing else. There is no FROM at that level, so a bare name there
is neither a column of some table nor ambiguous between two: it is an output name, and it is the
only kind of reference the position permits.

**Found in production, 2026-08-19.** Asked which buildings appear in EnergyCAP but not in
Archibus, the agent proposed the natural shape and was refused twice:

    SELECT code AS bldg_code, 'EnergyCAP only' AS side FROM ec WHERE ...
    UNION ALL
    SELECT code AS bldg_code, 'Archibus only' AS side FROM ab WHERE ...
    ORDER BY side, bldg_code
    -- run_query refused: bldg_code has no source to bind to: this scope selects from nothing

It recovered by deleting the ``ORDER BY``, so the turn cost 5 attempts and 142s and returned an
unordered 755-row answer. The refusal predates the ``selected_sources`` fix, which only changed
which wrong reason it gave (``r_ambiguous_reference`` before, ``r_unbound_reference`` after).

The cause was in ``ScopeView.output_aliases``: it returned ``frozenset()`` for any scope whose
expression is not an ``exp.Select``, and a set operation's scope expression is an
``exp.SetOperation``. So the ``ORDER BY <alias>`` branch in ``_bind_columns`` could never fire
for the one construct where an output name is the *only* legal spelling.

**The negative case is the load-bearing one.** Resolving these names must not turn the position
into a hole: a name that is not an output name still has nothing to bind to and must still
refuse. Widening this to "anything under a set operation's ORDER BY passes" would be a
fail-open, and it is the shape a careless fix takes.
"""

from __future__ import annotations

import sqlglot

from governed_bi.govern.binding import LayerRefusal, bind
from governed_bi.govern.scopes import iter_scopes


def _bind(sql: str):
    tree = sqlglot.parse_one(sql, read="postgres")
    return bind(iter_scopes(tree), default_schema="s")


def _rule(sql: str) -> str | None:
    out = _bind(sql)
    return out.rule_id if isinstance(out, LayerRefusal) else None


_CTES = (
    "WITH ec AS (SELECT DISTINCT property_cd AS code FROM s.usage), "
    "ab AS (SELECT DISTINCT property_code AS code FROM s.building) "
)


def test_the_production_shape_binds() -> None:
    """The statement the agent was refused twice, reduced to its skeleton."""
    sql = (
        _CTES
        + "SELECT code AS bldg_code, 'ec only' AS side FROM ec "
        "UNION ALL "
        "SELECT code AS bldg_code, 'ab only' AS side FROM ab "
        "ORDER BY side, bldg_code"
    )
    assert _rule(sql) is None, f"refused {_rule(sql)}: this is what Postgres requires here"


def test_an_output_name_that_is_not_an_alias_binds() -> None:
    """``SELECT code FROM ...`` names its output ``code`` without an ``AS``. Postgres accepts
    ``ORDER BY code`` on the union, so a fix keyed only on explicit ``exp.Alias`` nodes would
    still refuse the commonest spelling of all."""
    sql = "SELECT code FROM s.usage UNION SELECT code FROM s.building ORDER BY code"
    assert _rule(sql) is None


def test_except_and_intersect_resolve_the_same_way() -> None:
    """``exp.Union`` is one subclass of ``exp.SetOperation``; the rule is the operation's, not
    the keyword's. Keying the fix on ``Union`` alone would leave two constructs refusing."""
    for op in ("EXCEPT", "INTERSECT"):
        sql = f"SELECT a AS x FROM s.usage {op} SELECT b AS x FROM s.building ORDER BY x"
        assert _rule(sql) is None, f"{op} refused {_rule(sql)}"


def test_a_three_way_union_takes_its_names_from_the_leftmost_branch() -> None:
    """Nested set operations are left-associative, so the output names come from the innermost
    left branch — one level of recursion, not the immediate child."""
    sql = (
        "SELECT a AS x FROM s.usage UNION SELECT b AS x FROM s.building "
        "UNION SELECT c AS x FROM s.rooms ORDER BY x"
    )
    assert _rule(sql) is None


def test_a_name_that_is_not_an_output_name_still_refuses() -> None:
    """The fail-open guard, and the reason this file is not three assertions long.

    ``zzz`` is not in the output list and there is no FROM at this level, so nothing can bind
    it — Postgres raises too. A fix that let the ``ORDER BY`` of a set operation accept any bare
    name would pass every test above while removing the layer from the position entirely.
    """
    sql = "SELECT a AS x FROM s.usage UNION SELECT b AS x FROM s.building ORDER BY zzz"
    assert _rule(sql) == "r_unbound_reference"


def test_a_branch_column_name_is_not_an_output_name() -> None:
    """The subtler half of the same guard. ``a`` is a real column of a real table in the left
    branch, but the union's output is named ``x``, and Postgres resolves this position against
    output names only — ``ORDER BY a`` is an error there, not a reach into the branch.
    """
    sql = "SELECT a AS x FROM s.usage UNION SELECT b AS x FROM s.building ORDER BY a"
    assert _rule(sql) == "r_unbound_reference"
