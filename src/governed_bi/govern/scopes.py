"""One scope traversal for the function and binding layers. See ADR 0006. ``scope.columns`` misses bare ``HAVING``. This
module classifies; it does not refuse."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from sqlglot import expressions as exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from .identifiers import fold

__all__ = ["ScopeView", "scope_nodes", "iter_scopes", "SCOPE_BOUNDARY_NODES"]

#: Node types that start a new scope. Pruning here is what makes the walk local.
#: Anything not listed belongs to the scope that contains it.
SCOPE_BOUNDARY_NODES: tuple[type[exp.Expr], ...] = (
    exp.Select, exp.Union, exp.Subquery, exp.CTE,
)


def scope_nodes(scope: Scope) -> Iterator[exp.Expr]:
    """Nodes that belong to ``scope`` itself, nested scopes excluded.

    ``iter_expressions()`` rather than named args: those names move between sqlglot releases.
    """
    root = scope.expression
    stack: list[exp.Expr] = [root]
    while stack:
        node = stack.pop()
        if node is not root and isinstance(node, SCOPE_BOUNDARY_NODES):
            continue
        yield node
        stack.extend(node.iter_expressions())


@dataclass(frozen=True, slots=True)
class ScopeView:
    """One scope, its own nodes, and the names its sources answer to."""

    scope: Scope
    nodes: tuple[exp.Expr, ...]
    #: Folded alias of every source, plus the bare table name when a base source is not
    #: aliased. ``json_agg(t)`` names a source, not a column, and nothing else in the AST
    #: distinguishes that from a column reference.
    source_names: frozenset[str]

    @property
    def expression(self) -> exp.Expr:
        return self.scope.expression

    def columns(self) -> Iterator[exp.Column]:
        """Every ``Column`` in this scope, including bare ``HAVING`` ones ``scope.columns`` omits."""
        for node in self.nodes:
            if isinstance(node, exp.Column):
                yield node

    def functions(self) -> Iterator[exp.Func]:
        """Typed subclasses and ``Anonymous``. Matching only ``Anonymous`` omits ``CASE`` and ``CAST``."""
        for node in self.nodes:
            if isinstance(node, exp.Func):
                yield node

    def output_aliases(self) -> frozenset[str]:
        """Explicit aliases on a ``Select``. A set operation lists every output name, aliased or not: Postgres resolves
        that ``ORDER BY`` against the output list alone."""
        expression = self.scope.expression
        if isinstance(expression, exp.SetOperation):
            return frozenset(fold(name) for name in expression.named_selects if name)
        if not isinstance(expression, exp.Select):
            return frozenset()
        return frozenset(
            fold(projection.alias)
            for projection in expression.expressions
            if isinstance(projection, exp.Alias) and projection.alias
        )


def _source_names(scope: Scope) -> frozenset[str]:
    names: set[str] = set()
    for alias, source in scope.sources.items():
        if alias:
            names.add(fold(alias))
        if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
            names.add(fold(source.name))
    return frozenset(names)


def iter_scopes(tree: exp.Expr) -> tuple[ScopeView, ...]:
    """Innermost first. Children precede parents, which is why binding runs two passes."""
    return tuple(
        ScopeView(scope=scope, nodes=tuple(scope_nodes(scope)), source_names=_source_names(scope))
        for scope in traverse_scope(tree)
    )
