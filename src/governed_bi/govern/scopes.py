"""One scope traversal, shared by the function and binding layers (ADR 0006 §4).

Per-scope via ``traverse_scope`` / :func:`scope_nodes` — not a query-wide name
map, not ``scope.columns`` (misses bare ``HAVING``), not ``find_all`` on the
outer select. Every node is yielded by exactly one scope. Nothing here refuses;
classification stays separate from judgement so layer order stays a proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from sqlglot import expressions as exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from .identifiers import fold

__all__ = ["ScopeView", "scope_nodes", "iter_scopes", "SCOPE_BOUNDARY_NODES"]

#: Node types that start a new scope. Pruning at these is what makes the walk local.
#: ``Subquery`` and ``CTE`` wrap an inner ``Select`` that is its own scope; ``Union``
#: owns its two sides. Anything not listed belongs to the scope containing it.
SCOPE_BOUNDARY_NODES: tuple[type[exp.Expr], ...] = (
    exp.Select, exp.Union, exp.Subquery, exp.CTE,
)


def scope_nodes(scope: Scope) -> Iterator[exp.Expr]:
    """Every node belonging to ``scope`` itself, nested scopes excluded.

    Depth-first; prune at boundary nodes other than the root. Uses
    ``iter_expressions()`` (arg names move between sqlglot releases).
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
    #: Folded alias for every source, plus the bare table name where a base source is
    #: not aliased. What the whole-row argument rule (§2, closing B2) tests bare
    #: function arguments against: ``json_agg(t)`` names a *source*, not a column, and
    #: nothing else in the AST distinguishes it from a column reference.
    source_names: frozenset[str]

    @property
    def expression(self) -> exp.Expr:
        return self.scope.expression

    def columns(self) -> Iterator[exp.Column]:
        """Every ``Column`` node in this scope, including bare ``HAVING`` ones."""
        for node in self.nodes:
            if isinstance(node, exp.Column):
                yield node

    def functions(self) -> Iterator[exp.Func]:
        """Every function node in this scope: typed subclasses **and** ``Anonymous``.

        Matching only ``exp.Anonymous`` and matching all ``exp.Func`` are different
        allowlists (``CASE``/``CAST`` are typed nodes), and ``Anonymous`` is a subclass
        of ``Func``, so this is deliberately the wider of the two. ADR 0006 §2.
        """
        for node in self.nodes:
            if isinstance(node, exp.Func):
                yield node

    def output_aliases(self) -> frozenset[str]:
        """Folded projection aliases, for ``ORDER BY <alias>`` references."""
        expression = self.scope.expression
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
    """Every scope in ``tree``, innermost first, each with its own nodes.

    ``traverse_scope`` order: children before parents. Callers resolving against
    ancestor scopes (correlated subqueries) must build the whole map first, which is
    why :mod:`.binding` runs two passes.
    """
    return tuple(
        ScopeView(scope=scope, nodes=tuple(scope_nodes(scope)), source_names=_source_names(scope))
        for scope in traverse_scope(tree)
    )
