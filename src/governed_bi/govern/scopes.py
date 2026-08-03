"""One scope traversal, shared by the function layer and the binding layer.

ADR 0006 §4 records two mechanics v1 paid for, and both are here rather than in the
layer that uses them, because **each layer having its own walk is how two layers end
up disagreeing about what a reference means**:

* **Per-scope resolution via ``traverse_scope``, never a query-wide name map.** A CTE
  named after a base table deferred that table's excluded column: the flat map said
  "``customers`` is a table with an excluded ``ssn``", the query said "``customers``
  is my CTE", and the flat map won.
* **Iterate every ``Column`` node in the statement, not ``scope.columns``.** The
  latter omits bare ``HAVING`` references — a column the allowlist never saw.

``scope.expression.find_all(...)`` is not per-scope: called on an outer ``Select`` it
also returns everything inside nested subqueries, which is the query-wide map with
extra steps. :func:`scope_nodes` is the pruned walk that makes "every node in *this*
scope" mean what it says — every node is yielded by exactly one scope.

**Nothing here refuses.** Classification is separated from judgement on purpose: the
function layer is layer 3 and binding is layer 4, so if building the scope view could
refuse, a table-valued function in ``FROM`` would be blocked at BINDING *before*
FUNCTIONS ran — and then "reached layer 4" would no longer prove layer 3 passed,
which is the property the whole ordered stack rests on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from sqlglot import expressions as exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from .identifiers import fold

__all__ = ["ScopeView", "scope_nodes", "iter_scopes", "SCOPE_BOUNDARY_NODES"]

#: Node types that start a new scope. Pruning at these is what makes the walk local.
#:
#: ``exp.Subquery`` and ``exp.CTE`` are wrappers whose inner ``Select`` is its own
#: scope; ``exp.Union`` owns its two sides. Anything not listed belongs to the scope
#: that contains it.
SCOPE_BOUNDARY_NODES: tuple[type[exp.Expr], ...] = (
    exp.Select, exp.Union, exp.Subquery, exp.CTE,
)


def scope_nodes(scope: Scope) -> Iterator[exp.Expr]:
    """Every node belonging to ``scope`` itself, nested scopes excluded.

    Depth-first from the scope's own expression, pruning at a boundary node other
    than the root. Uses ``iter_expressions()`` rather than ``args`` keys because arg
    names move between sqlglot releases — ``Select``'s ``FROM`` is under ``from_`` in
    the pinned release and was ``from`` before it, and a walk keyed on the old name
    would have silently stopped seeing ``FROM`` sources.
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
    #: Folded alias for every source, plus the bare table name where a base source
    #: is not aliased. This is what the whole-row argument rule (§2, closing B2)
    #: tests a function's bare arguments against: ``json_agg(t)`` names a *source*,
    #: not a column, and that is the only thing distinguishing it from an ordinary
    #: column reference in the AST.
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

        ADR 0006 §2's third recorded defect: "every function call" is ambiguous
        against sqlglot, because matching only ``exp.Anonymous`` and matching all
        ``exp.Func`` are different allowlists, and ``CASE``/``CAST`` are typed nodes
        rather than ``Anonymous``. ``exp.Anonymous`` is a subclass of ``exp.Func``,
        so this is the wider of the two — deliberately.
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

    Order is ``traverse_scope``'s: children before parents. Callers that resolve
    references against ancestor scopes (correlated subqueries) must therefore build
    the whole map before resolving any of it, which is why :mod:`.binding` runs two
    passes.
    """
    return tuple(
        ScopeView(scope=scope, nodes=tuple(scope_nodes(scope)), source_names=_source_names(scope))
        for scope in traverse_scope(tree)
    )
