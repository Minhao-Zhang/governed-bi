"""Postgres resolves ``ORDER BY`` after a set operation against the output list alone. A name absent from that list
still refuses."""

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
    """A name absent from the output list still refuses. Accepting any bare name would remove the check."""
    sql = "SELECT a AS x FROM s.usage UNION SELECT b AS x FROM s.building ORDER BY zzz"
    assert _rule(sql) == "r_unbound_reference"


def test_a_branch_column_name_is_not_an_output_name() -> None:
    """The subtler half of the same guard. ``a`` is a real column of a real table in the left
    branch, but the union's output is named ``x``, and Postgres resolves this position against
    output names only — ``ORDER BY a`` is an error there, not a reach into the branch.
    """
    sql = "SELECT a AS x FROM s.usage UNION SELECT b AS x FROM s.building ORDER BY a"
    assert _rule(sql) == "r_unbound_reference"
