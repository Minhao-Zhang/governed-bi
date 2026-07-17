"""Five-layer SQL guardrails (Analyst step 8; Architecture section 6).

Ordered, fail-closed on any layer:

1. **syntax** parses as valid SQL (sqlglot).
2. **policy blacklist** no DDL/DML/PRAGMA/etc.; read-only single statement only.
3. **AST column allowlist** every referenced column is a known, non-excluded,
   non-``suspect`` column (dev/BIRD hard-blocks suspect; prod soft-warns).
4. **term-semantics** referenced assets match the bound terms.
5. **cost** structural cross-join / cartesian-product guard; numeric
   EXPLAIN-based cost (Postgres / Redshift) is future per-dialect work.

These run in the Analyst's ``wrap_tool_call`` middleware. The refuse-gate (D5)
runs *concurrently*, not as a sixth layer.

Fail-closed policy and its cost: several layers include deliberate policy blocks
that refuse rather than risk a leak - L2 rejects anything but a single read-only
statement; L3 blocks star projections and unqualified columns in a mixed
base+derived scope (the allowlist cannot vouch for columns the query never
enumerates or attribute a bare name it cannot resolve); L4/L5 block NATURAL joins
and cross-namespace / db-qualified names. Each trades a false-refusal cost for
zero column leakage. That cost is not meant to be paid by the user: a
feedback-aware generator recovers via the Analyst's repair loop (the block is fed
back as ``RepairFeedback`` and the SQL is regenerated), and the eval's refuse-gate
``false_refusal_rate`` is the counterweight metric that keeps the blocks honest.

Build status: all five layers are enforced. L4 (term-semantics) runs only when
the caller passes ``allowed_tables`` (the Analyst's retrieval scope); with no
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
    """Build the L3 allowlist from a corpus (pass the ``for_analyst()`` view).

    Physical names are used because the SQL under inspection is in the live
    (obfuscated) identifiers, not asset ids.

    Keys are three-part ``{schema}.{physical_name}.{column}`` (schema = the table's
    ``schema`` field) — the engine is uniformly schema-qualified, so a same-named
    column in two schemas never collides.
    """
    allowed: set[str] = set()
    suspect: set[str] = set()
    for asset in corpus.assets:
        if not isinstance(asset, TableAsset) or asset.governance.excluded:
            continue
        prefix = f"{asset.schema}.{asset.physical_name}"
        for col in asset.columns:
            if col.governance.excluded:
                continue
            ref = f"{prefix}.{col.physical_name}"
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


def _physical_of(src: exp.Table, default_schema: str | None) -> str:
    """The schema-qualified physical identity ``{schema}.{table}`` of a base source.

    The schema is the source's own ``db`` qualifier or, for a bare reference, the
    designated ``default_schema`` (empty string when neither is known, so it fails
    closed against the allowlist).
    """
    schema = src.db or default_schema or ""
    return f"{schema}.{src.name}"


def _scope_sources(
    scope: object, *, default_schema: str | None = None
) -> tuple[dict[str, str | None], set[str], set[str]]:
    """Resolve one scope's sources (never flattened across the query).

    Returns ``(resolved, base, derived_outputs)``:

    - ``resolved`` maps a source name (alias or physical) to its physical base
      table, or ``None`` if the source is a derived CTE / subquery.
    - ``base`` is the set of base physical table names in the scope.
    - ``derived_outputs`` is the set of column names the scope's derived sources
      project, used to validate a bare column that can only come from one of them.

    Physical identities are schema-qualified ``{schema}.{table}`` (see
    :func:`_physical_of`).
    """
    resolved: dict[str, str | None] = {}
    base: set[str] = set()
    derived_outputs: set[str] = set()
    for name, src in scope.sources.items():
        if isinstance(src, exp.Table):
            physical = _physical_of(src, default_schema)
            resolved[name] = physical
            resolved.setdefault(src.name, physical)
            base.add(physical)
        else:  # a nested Scope: a CTE or subquery
            resolved[name] = None
            derived_outputs.update(getattr(src.expression, "named_selects", []) or [])
    return resolved, base, derived_outputs


def _layer_columns(
    root: exp.Expression,
    allowed: set[str],
    suspect: set[str],
    hard_block_suspect: bool,
    *,
    default_schema: str | None = None,
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
    cache = {
        id(scope): _scope_sources(scope, default_schema=default_schema)
        for scope in scopes
    }

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
            if column.db:
                # An explicit ``schema.table.column`` reference. Fail closed unless
                # that (schema, table) is actually a source in this query's FROM:
                # otherwise a column of an off-scope table would slip past L3 (its
                # key is in the corpus-wide allowlist) and L4 (which inspects only
                # FROM sources), leaving the database engine as the last line.
                physical: str | None = f"{column.db}.{qualifier}"
                if physical not in cache[id(scope)][1]:
                    return _fail(layer, f"column references a table not in scope: {physical}.{name}")
            else:
                physical = resolve(scope, qualifier)
                if physical is _MISSING:
                    return _fail(layer, f"column references unknown source '{qualifier}'")
                if physical is None:
                    continue  # derived source; its base columns are validated in its scope
            ref = f"{physical}.{name}"
            if ref in suspect:
                if hard_block_suspect:
                    return _fail(layer, f"suspect (unreliable) column blocked: {ref}")
                continue
            if ref in allowed:
                continue
            return _fail(layer, f"column not in the allowlist: {ref}")

        # Bare column: only this scope's own sources can own it.
        _resolved, base, derived_outputs = cache[id(scope)]
        candidate_allowed = any(f"{p}.{name}" in allowed for p in base)
        candidate_suspect = any(f"{p}.{name}" in suspect for p in base)
        # Same-named columns across in-scope schemas are routine, so a bare name
        # matching a suspect column in ANY in-scope base must fail closed: the DB
        # could bind it to the decoy (leftmost-table resolution) and the caller
        # should qualify instead.
        block_suspect = candidate_suspect
        if hard_block_suspect and block_suspect:
            return _fail(layer, f"suspect (unreliable) column blocked: {name}")
        if candidate_allowed or candidate_suspect:
            continue
        if base:
            return _fail(layer, f"column not in the allowlist: {name}")
        if name in derived_outputs:
            continue  # projected by an in-scope derived source (validated there)
        return _fail(layer, f"column not in the allowlist: {name}")

    # Join keys that are not exp.Column nodes: USING (col) identifiers and NATURAL
    # joins. These reference columns that the find_all(exp.Column) pass never sees.
    for join in root.find_all(exp.Join):
        if join.args.get("method") == "NATURAL" or join.args.get("kind") == "NATURAL":
            # NATURAL joins on every common column, including ones we cannot
            # enumerate against the allowlist. Fail closed; require explicit keys.
            return _fail(layer, "NATURAL JOIN is not allowed; use an explicit ON/USING clause")
        using = join.args.get("using")
        if not using:
            continue
        select = join.find_ancestor(exp.Select)
        scope = by_select.get(id(select)) if select is not None else None
        if scope is None:
            return _fail(layer, "cannot attribute a USING join to a query scope")
        _resolved, base, _derived = cache[id(scope)]
        for identifier in using:
            key = identifier.name
            candidate_allowed = any(f"{p}.{key}" in allowed for p in base)
            candidate_suspect = any(f"{p}.{key}" in suspect for p in base)
            if hard_block_suspect and candidate_suspect and not candidate_allowed:
                return _fail(layer, f"suspect (unreliable) column blocked: {key}")
            if candidate_allowed or candidate_suspect:
                continue
            return _fail(layer, f"column not in the allowlist: {key}")

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


def _layer_cartesian(
    root: exp.Expression, *, default_schema: str | None = None
) -> GuardrailVerdict:
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
            name: _physical_of(src, default_schema)
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
        # cannot attribute simply adds no edge. The self-join count keys on the
        # schema-qualified physical identity, so a cross-schema same-name pair
        # (schema_a.orders vs schema_b.orders) is two distinct tables, not a
        # self-join, and each bare qualifier still maps to its own source.
        ref: dict[str, str] = {}
        physical_uses = Counter(
            _physical_of(src, default_schema)
            for src in scope.sources.values()
            if isinstance(src, exp.Table)
        )
        for name, src in scope.sources.items():
            ref[name] = name
            if isinstance(src, exp.Table):
                physical = _physical_of(src, default_schema)
                if src.name != name and physical_uses[physical] == 1:
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


def _layer_terms(
    root: exp.Expression,
    allowed_tables: set[str],
    *,
    default_schema: str | None = None,
) -> GuardrailVerdict:
    """L4: every base table the query touches is within the retrieved scope.

    ``allowed_tables`` is the set of table names the analyst licensed for this
    question: the tables surfaced by retrieval and their join-plan Steiner points
    (see ``analyst.agent``, ``analyst.governance._licensed_table_ids``). A base table
    outside that set means the SQL wandered past the semantically grounded scope,
    so it is blocked fail-closed.

    Scope-aware (via ``traverse_scope``): a real base table is a ``Table`` source
    in some scope, while a CTE is a derived ``Scope`` in the scope that references
    it. Checking only ``Table`` sources means a nested CTE cannot borrow an
    out-of-scope table's name to slip that table past the gate.

    The licensed names are schema-qualified ``{schema}.{table}``. A schema-qualified
    reference is allowed when its ``(schema, table)`` is in the licensed set; a
    three-part ``catalog.schema.table`` is still rejected (one database). A *bare*
    reference resolves ONLY to the designated ``default_schema`` (falling back to the
    sole licensed schema when no default is configured) and is REFUSED AS AMBIGUOUS
    when the licensed set holds that bare name in more than one schema — this is what
    forbids a self-authorized off-scope schema.
    """
    layer = GuardrailLayer.term_semantics

    # Index the licensed qualified names by their bare table name so
    # a bare reference can be resolved / flagged as cross-schema-ambiguous.
    schemas_by_name: dict[str, set[str]] = {}
    for qualified in allowed_tables:
        schema, _, table = qualified.rpartition(".")
        schemas_by_name.setdefault(table, set()).add(schema)

    for scope in traverse_scope(root):
        for src in scope.sources.values():
            if not isinstance(src, exp.Table):
                continue
            if src.catalog:
                # A three-part catalog.schema.table still names one database; a
                # catalog qualifier reaches outside it. Fail closed.
                return _fail(layer, f"cross-catalog table reference not allowed: {src.sql()}")
            if src.db:
                key = f"{src.db}.{src.name}"
                if key not in allowed_tables:
                    return _fail(layer, f"table outside the retrieved scope: {key}")
                continue
            # Bare reference: resolve to the default schema; refuse if ambiguous.
            schemas = schemas_by_name.get(src.name, set())
            if len(schemas) > 1:
                return _fail(
                    layer,
                    f"ambiguous unqualified table '{src.name}' present in schemas "
                    f"{sorted(schemas)}; qualify it with a schema",
                )
            resolved = default_schema if default_schema is not None else (
                next(iter(schemas)) if len(schemas) == 1 else None
            )
            if resolved is None or f"{resolved}.{src.name}" not in allowed_tables:
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
    default_schema: str | None = None,
) -> GuardrailVerdict:
    """Run the layers in order; return on the first failure (fail-closed).

    ``allowed_columns`` / ``suspect_columns`` are physical schema-qualified
    ``schema.table.column`` references (build them with :func:`column_allowlist`).
    ``hard_block_suspect`` is the dev/prod suspect toggle. ``allowed_tables``
    (qualified ``schema.table`` names) drives L4 (term-semantics); when ``None``,
    L4 is skipped (e.g. a corpus-only unit check with no retrieval scope).
    ``dialect`` is the sqlglot dialect name (e.g. ``"sqlite"``) for parsing.

    The engine is uniformly schema-qualified: allowlist keys, licensed table names,
    and layer bookkeeping are all ``schema.``-prefixed (see :func:`column_allowlist`,
    :func:`_layer_terms`, :func:`_layer_columns`, :func:`_layer_cartesian`), and
    ``default_schema`` is the schema a bare (unqualified) reference resolves to.
    """
    verdict, statements = _layer_syntax(sql, dialect)
    if not verdict.passed:
        return verdict

    verdict = _layer_policy(statements)
    if not verdict.passed:
        return verdict

    verdict = _layer_columns(
        statements[0],
        allowed_columns,
        set(suspect_columns),
        hard_block_suspect,
        default_schema=default_schema,
    )
    if not verdict.passed:
        return verdict

    if allowed_tables is not None:
        verdict = _layer_terms(
            statements[0],
            set(allowed_tables),
            default_schema=default_schema,
        )
        if not verdict.passed:
            return verdict

    return _layer_cartesian(statements[0], default_schema=default_schema)
