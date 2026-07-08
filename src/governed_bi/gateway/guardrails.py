"""Five-layer SQL guardrails (Server step 8; Architecture section 6).

Ordered, fail-closed on any layer:

1. **syntax** parses as valid SQL (sqlglot).
2. **policy blacklist** no DDL/DML/PRAGMA/etc.; read-only single statement only.
3. **AST column allowlist** every referenced column is a known, non-excluded,
   non-``suspect`` column (dev/BIRD hard-blocks suspect; prod soft-warns).
4. **term-semantics** referenced assets match the bound terms.
5. **cost** structural cross-join / cartesian-product guard; numeric
   EXPLAIN-based cost (Postgres / Redshift) is future per-dialect work.

These run in the server's ``wrap_tool_call`` middleware. The refuse-gate (D5)
runs *concurrently*, not as a sixth layer.

Build status: all five layers are enforced. L4 (term-semantics) runs only when
the caller passes ``allowed_tables`` (the server's retrieval scope); with no
scope it is skipped, so a corpus-only unit check still exercises L1 to L3 and L5.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import traverse_scope

from ..corpus.schemas import ReliabilityStatus, TableAsset

if TYPE_CHECKING:
    from ..corpus import Corpus


class GuardrailLayer(str, Enum):
    syntax = "syntax"
    policy_blacklist = "policy_blacklist"
    ast_column_allowlist = "ast_column_allowlist"
    term_semantics = "term_semantics"
    cost_estimate = "cost_estimate"


@dataclass(frozen=True)
class GuardrailVerdict:
    passed: bool
    failed_layer: GuardrailLayer | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ColumnAllowlist:
    """The columns L3 permits, as physical ``table.column`` references.

    ``allowed`` are safe columns; ``suspect`` are the curator-flagged decoys that
    dev/BIRD hard-blocks and prod/enterprise soft-warns on. ``governance.excluded``
    columns and tables appear in neither set, so they are always blocked.
    """

    allowed: frozenset[str]
    suspect: frozenset[str]


def column_allowlist(corpus: "Corpus") -> ColumnAllowlist:
    """Build the L3 allowlist from a corpus (pass the ``for_server()`` view).

    Physical names are used because the SQL under inspection is in the live
    (obfuscated) identifiers, not asset ids.
    """
    allowed: set[str] = set()
    suspect: set[str] = set()
    for asset in corpus.assets:
        if not isinstance(asset, TableAsset) or asset.governance.excluded:
            continue
        for col in asset.columns:
            if col.governance.excluded:
                continue
            ref = f"{asset.physical_name}.{col.physical_name}"
            if col.reliability.status is ReliabilityStatus.suspect:
                suspect.add(ref)
            else:
                allowed.add(ref)
    return ColumnAllowlist(frozenset(allowed), frozenset(suspect))


# Expression types that make a statement more than a read-only query. Resolved by
# name so a missing class in some sqlglot version does not break the import.
_FORBIDDEN_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Drop",
    "Alter",
    "TruncateTable",
    "Command",  # VACUUM, SET, and other bare commands
    "Pragma",
    "Into",  # SELECT ... INTO writes a table
    "Copy",  # bulk load/unload (Postgres / Redshift)
    "Grant",
)
_FORBIDDEN_TYPES = tuple(
    t for name in _FORBIDDEN_NAMES if (t := getattr(exp, name, None)) is not None
)

# A read-only statement roots at a query: a SELECT or a set operation over selects.
_QUERY_ROOTS = (exp.Select, exp.SetOperation)


def _pass() -> GuardrailVerdict:
    return GuardrailVerdict(passed=True)


def _fail(layer: GuardrailLayer, reason: str) -> GuardrailVerdict:
    return GuardrailVerdict(passed=False, failed_layer=layer, reason=reason)


def _layer_syntax(sql: str, dialect: str | None) -> tuple[GuardrailVerdict, list[exp.Expression]]:
    """L1: parse. Returns the verdict and, on success, the parsed statements.

    Catches the whole ``SqlglotError`` family (both ``ParseError`` and the sibling
    ``TokenError`` raised on unterminated literals / stray delimiters), so malformed
    SQL always yields a fail-closed verdict instead of an unhandled exception.
    """
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except SqlglotError as err:
        first = str(err).splitlines()[0] if str(err) else "parse error"
        return _fail(GuardrailLayer.syntax, f"does not parse as SQL: {first}"), []
    if not statements:
        return _fail(GuardrailLayer.syntax, "no SQL statement found"), []
    return _pass(), statements


def _layer_policy(statements: list[exp.Expression]) -> GuardrailVerdict:
    """L2: read-only single statement, no DDL/DML/PRAGMA/command."""
    if len(statements) != 1:
        return _fail(
            GuardrailLayer.policy_blacklist,
            f"exactly one statement allowed, got {len(statements)}",
        )
    root = statements[0]
    if not isinstance(root, _QUERY_ROOTS):
        return _fail(
            GuardrailLayer.policy_blacklist,
            f"not a read-only query (top-level {type(root).__name__})",
        )
    # Defense in depth: reject a write/command anywhere in the tree (e.g. hidden
    # in a CTE or subquery), not only at the root.
    for node in root.walk():
        if isinstance(node, _FORBIDDEN_TYPES):
            return _fail(
                GuardrailLayer.policy_blacklist,
                f"forbidden statement type: {type(node).__name__}",
            )
    return _pass()


_MISSING = object()  # sentinel: a source name that is not known anywhere in the query


def _is_projection_star(select: exp.Select) -> bool:
    """Whether the SELECT projects a star (``*`` or ``table.*``).

    A bare ``*`` is an ``exp.Star``; ``table.*`` is an ``exp.Column`` named ``*``.
    ``COUNT(*)`` is an ``exp.Count`` (its star is nested), so it is not flagged.
    """
    for projection in select.expressions:
        node = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(node, exp.Star) or (isinstance(node, exp.Column) and node.name == "*"):
            return True
    return False


def _scope_sources(scope: object) -> tuple[dict[str, str | None], set[str], set[str]]:
    """Resolve one scope's sources (never flattened across the query).

    Returns ``(resolved, base, derived_outputs)``:

    - ``resolved`` maps a source name (alias or physical) to its physical base
      table, or ``None`` if the source is a derived CTE / subquery.
    - ``base`` is the set of base physical table names in the scope.
    - ``derived_outputs`` is the set of column names the scope's derived sources
      project, used to validate a bare column that can only come from one of them.
    """
    resolved: dict[str, str | None] = {}
    base: set[str] = set()
    derived_outputs: set[str] = set()
    for name, src in scope.sources.items():
        if isinstance(src, exp.Table):
            resolved[name] = src.name
            resolved.setdefault(src.name, src.name)
            base.add(src.name)
        else:  # a nested Scope: a CTE or subquery
            resolved[name] = None
            derived_outputs.update(getattr(src.expression, "named_selects", []) or [])
    return resolved, base, derived_outputs


def _layer_columns(
    root: exp.Expression,
    allowed: set[str],
    suspect: set[str],
    hard_block_suspect: bool,
) -> GuardrailVerdict:
    """L3: every referenced column resolves to an allowed physical column.

    Scope-aware (via ``traverse_scope``), and deliberately resolved **per scope**
    (walking up the parent chain for correlated references) rather than through a
    query-wide map: a flattened map lets a CTE/subquery name in one scope poison a
    base table of the same name in another. Every ``exp.Column`` in the statement
    is checked (not just ``scope.columns``, which omits bare ``HAVING`` refs).

    - A star projection (``SELECT *`` / ``t.*``) is blocked: the allowlist cannot
      vouch for columns a query never enumerates.
    - A qualified column resolves through its own scope's sources (then outward);
      a derived source defers to the scope that defines it.
    - A bare column is judged against its own scope's base tables. If it matches no
      base column and a base table is present, it is blocked (require
      qualification); in a derived-only scope it must be a projected output of a
      derived source.
    - A ``suspect`` column is hard-blocked when ``hard_block_suspect`` (dev/BIRD).
    """
    layer = GuardrailLayer.ast_column_allowlist
    scopes = list(traverse_scope(root))

    for scope in scopes:
        select = scope.expression
        if isinstance(select, exp.Select) and _is_projection_star(select):
            return _fail(layer, "star projection is not allowed; enumerate columns")

    by_select = {id(scope.expression): scope for scope in scopes}
    cache = {id(scope): _scope_sources(scope) for scope in scopes}

    def resolve(scope: object, qualifier: str) -> object:
        # Walk up the scope chain so a correlated reference resolves against the
        # outer scope that owns it.
        current = scope
        while current is not None:
            resolved = cache[id(current)][0]
            if qualifier in resolved:
                return resolved[qualifier]
            current = current.parent
        return _MISSING

    for column in root.find_all(exp.Column):
        name = column.name
        if name == "*":  # a t.* anywhere (bare * is caught by the star check above)
            return _fail(layer, "star projection is not allowed; enumerate columns")

        select = column.find_ancestor(exp.Select)
        scope = by_select.get(id(select)) if select is not None else None
        if scope is None:
            return _fail(layer, f"cannot attribute column '{name}' to a query scope")

        qualifier = column.table
        if qualifier:
            physical = resolve(scope, qualifier)
            if physical is _MISSING:
                return _fail(layer, f"column references unknown source '{qualifier}'")
            if physical is None:
                continue  # derived source; its base columns are validated in its scope
            ref = f"{physical}.{name}"
            if ref in suspect:
                if hard_block_suspect:
                    return _fail(layer, f"suspect (decoy) column blocked: {ref}")
                continue
            if ref in allowed:
                continue
            return _fail(layer, f"column not in the allowlist: {ref}")

        # Bare column: only this scope's own sources can own it.
        _resolved, base, derived_outputs = cache[id(scope)]
        candidate_allowed = any(f"{p}.{name}" in allowed for p in base)
        candidate_suspect = any(f"{p}.{name}" in suspect for p in base)
        if hard_block_suspect and candidate_suspect and not candidate_allowed:
            return _fail(layer, f"suspect (decoy) column blocked: {name}")
        if candidate_allowed or candidate_suspect:
            continue
        if base:
            return _fail(layer, f"column not in the allowlist: {name}")
        if name in derived_outputs:
            continue  # projected by an in-scope derived source (validated there)
        return _fail(layer, f"column not in the allowlist: {name}")

    return _pass()


def _source_key(node: exp.Expression | None) -> str | None:
    """The in-scope name of a FROM source: its alias, else (for a base table) its
    physical name. This matches the keys sqlglot's scope resolver uses and the
    ``table`` qualifier carried on a column."""
    if isinstance(node, exp.Table):
        return node.alias or node.name
    if isinstance(node, exp.Subquery):
        return node.alias or None
    return None


def _from_primary(select: exp.Select) -> str | None:
    """The source key of the query's leading FROM source (before any JOINs)."""
    from_node = select.args.get("from_") or select.args.get("from")
    return _source_key(from_node.this) if from_node is not None else None


def _equality_conjuncts(predicate: exp.Expression):
    """Yield the ``=`` comparisons joined by top-level ``AND`` in a predicate.

    Descends only through ``AND`` and parentheses, so nothing inside an ``OR``
    branch or a nested subquery is mistaken for a connecting predicate.
    """
    stack = [predicate]
    while stack:
        node = stack.pop()
        if isinstance(node, exp.And):
            stack.append(node.this)
            stack.append(node.expression)
        elif isinstance(node, exp.Paren):
            stack.append(node.this)
        elif isinstance(node, exp.EQ):
            yield node


def _uf_find(parent: dict[str, str], node: str) -> str:
    while parent[node] != node:
        parent[node] = parent[parent[node]]
        node = parent[node]
    return node


def _uf_union(parent: dict[str, str], a: str, b: str) -> None:
    parent[_uf_find(parent, a)] = _uf_find(parent, b)


def _layer_cartesian(root: exp.Expression) -> GuardrailVerdict:
    """L5: structural cost guard against unconstrained cross joins.

    Analysed per query scope to avoid false positives. Within a scope, the base
    physical tables joined in the FROM/JOINs are graph nodes; an equality
    predicate that links a column of one to a column of another - a JOIN ``ON``
    conjunct or a top-level ``WHERE`` conjunct ``a.x = b.y`` - is an edge. If a
    scope joins two or more base tables that are not all connected into one
    component, the join is an (accidental) cartesian product and is blocked
    fail-closed. A comma join whose linking predicate lives in ``WHERE`` is
    legitimate and passes.

    Derived sources (CTEs / subqueries) are each their own scope; they are never
    required nodes here, but may bridge base tables that join only through them.
    Predicates that cannot be reliably attributed simply add no edge, so the only
    block is the clear case: two or more base tables with no connecting equality.

    This is a deterministic structural guard; numeric EXPLAIN-based cost
    (Postgres / Redshift) is future per-dialect work.
    """
    layer = GuardrailLayer.cost_estimate

    for scope in traverse_scope(root):
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue  # e.g. a set-operation scope has no FROM of its own

        base = {
            name: src.name
            for name, src in scope.sources.items()
            if isinstance(src, exp.Table)
        }
        if len(base) < 2:
            continue  # one base table (or none) cannot cross-join

        # Union-find over every source in scope; derived sources may act as
        # bridges even though only base tables must end up connected.
        parent = {name: name for name in scope.sources}

        # A column's ``table`` qualifier is an alias or a physical name; map both
        # to the source key, but leave an ambiguous physical name (a self-join
        # reuses one physical table under two aliases) unmapped so a predicate we
        # cannot attribute simply adds no edge.
        ref: dict[str, str] = {}
        physical_uses = Counter(
            src.name for src in scope.sources.values() if isinstance(src, exp.Table)
        )
        for name, src in scope.sources.items():
            ref[name] = name
            if isinstance(src, exp.Table) and src.name != name and physical_uses[src.name] == 1:
                ref.setdefault(src.name, name)

        def node_of(column: exp.Column) -> str | None:
            return ref.get(column.table) if column.table else None

        predicates: list[exp.Expression] = []
        where = select.args.get("where")
        if where is not None:
            predicates.append(where.this)

        left_root = _from_primary(select)
        for join in select.args.get("joins") or []:
            on = join.args.get("on")
            if on is not None:
                predicates.append(on)
            # USING is equality sugar on the shared columns: link the joined
            # source to the accumulated left side so it is not seen as unjoined.
            if join.args.get("using") and left_root is not None:
                joined = _source_key(join.this)
                if joined in parent and left_root in parent:
                    _uf_union(parent, joined, left_root)

        for predicate in predicates:
            for eq in _equality_conjuncts(predicate):
                left, right = eq.this, eq.expression
                if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                    a, b = node_of(left), node_of(right)
                    if a is not None and b is not None and a != b:
                        _uf_union(parent, a, b)

        if len({_uf_find(parent, name) for name in base}) > 1:
            tables = ", ".join(sorted({phys for phys in base.values()}))
            return _fail(layer, f"unconstrained cross join between tables: {tables}")

    return _pass()


def _layer_terms(root: exp.Expression, allowed_tables: set[str]) -> GuardrailVerdict:
    """L4: every base table the query touches is within the retrieved scope.

    ``allowed_tables`` is the set of physical table names the server licensed for
    this question: the tables surfaced by retrieval and their join-plan Steiner
    points (see ``server.flow``). A base table outside that set means the SQL
    wandered past the semantically grounded scope, so it is blocked fail-closed.

    Scope-aware (via ``traverse_scope``): a real base table is a ``Table`` source
    in some scope, while a CTE is a derived ``Scope`` in the scope that references
    it. Checking only ``Table`` sources means a nested CTE cannot borrow an
    out-of-scope table's name to slip that table past the gate.
    """
    layer = GuardrailLayer.term_semantics
    for scope in traverse_scope(root):
        for src in scope.sources.values():
            if not isinstance(src, exp.Table):
                continue
            if src.db or src.catalog:
                # The connection is a single database; a schema/catalog-qualified
                # name reaches outside the licensed namespace. Fail closed.
                return _fail(layer, f"cross-namespace table reference not allowed: {src.sql()}")
            if src.name not in allowed_tables:
                return _fail(layer, f"table outside the retrieved scope: {src.name}")
    return _pass()


def check(
    sql: str,
    *,
    allowed_columns: set[str],
    hard_block_suspect: bool,
    suspect_columns: frozenset[str] = frozenset(),
    allowed_tables: frozenset[str] | None = None,
    dialect: str | None = None,
) -> GuardrailVerdict:
    """Run the layers in order; return on the first failure (fail-closed).

    ``allowed_columns`` / ``suspect_columns`` are physical ``table.column``
    references (build them with :func:`column_allowlist`). ``hard_block_suspect``
    is the dev/prod suspect toggle. ``allowed_tables`` (physical table names)
    drives L4 (term-semantics); when ``None``, L4 is skipped (e.g. a
    corpus-only unit check with no retrieval scope). ``dialect`` is the sqlglot
    dialect name (e.g. ``"sqlite"``) for parsing.
    """
    verdict, statements = _layer_syntax(sql, dialect)
    if not verdict.passed:
        return verdict

    verdict = _layer_policy(statements)
    if not verdict.passed:
        return verdict

    verdict = _layer_columns(
        statements[0], allowed_columns, set(suspect_columns), hard_block_suspect
    )
    if not verdict.passed:
        return verdict

    if allowed_tables is not None:
        verdict = _layer_terms(statements[0], set(allowed_tables))
        if not verdict.passed:
            return verdict

    return _layer_cartesian(statements[0])
