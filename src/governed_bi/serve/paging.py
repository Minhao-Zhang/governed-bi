"""A statement that re-runs an earlier one with ``OFFSET`` reads a page of it; it does not answer.

``run_query`` shows the agent 20 rows. Agents read further with ``LIMIT 20 OFFSET 20``, and
delivering that statement graded one page of the answer: every such eval answer was wrong.
"""

from __future__ import annotations

from collections.abc import Iterable

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

__all__ = ["is_page_of"]


def is_page_of(statement: str | None, earlier: Iterable[str | None], *, dialect: str | None = None) -> bool:
    """True when ``statement`` has an outer ``OFFSET`` above zero and is otherwise an earlier statement.

    ``ORDER BY`` is ignored too: agents add one to page stably. ``OFFSET 0`` and a bare ``LIMIT`` are not
    pages, since ``ORDER BY x LIMIT 1`` after a full list is how a top-1 answer is written.
    """
    tree = _parse(statement, dialect)
    if tree is None or not _offset_above_zero(tree):
        return False
    key = _without_paging(tree, dialect)
    for other in earlier:
        other_tree = _parse(other, dialect)
        if other_tree is not None and _without_paging(other_tree, dialect) == key:
            return True
    return False


def _parse(statement: str | None, dialect: str | None) -> exp.Expr | None:
    if not statement or not statement.strip():
        return None
    try:
        return sqlglot.parse_one(statement, dialect=dialect)
    except SqlglotError:
        return None


def _offset_above_zero(tree: exp.Expr) -> bool:
    offset = tree.args.get("offset")
    if offset is None:
        return False
    value = offset.expression if isinstance(offset, exp.Offset) else offset
    try:
        return int(str(value.this if isinstance(value, exp.Literal) else value)) > 0
    except (TypeError, ValueError):
        # A computed offset is still paging.
        return True


def _without_paging(tree: exp.Expr, dialect: str | None) -> str:
    bare = tree.copy()
    for arg in ("limit", "offset", "order"):
        bare.set(arg, None)
    return bare.sql(dialect=dialect)
