"""Six layers, first failure wins. See ADR 0006. A missing security argument raises. An empty retrieved set allows
nothing. An exception inside the walk is :data:`~governed_bi.govern.layers.GUARDRAIL_ERROR`, not a parse rule."""

from __future__ import annotations

from typing import Iterator, Sequence

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

from ..corpus.analyst import AnalystCorpus
from .binding import Bindings, LayerRefusal, bind
from .functions import canonical_function_name
from .identifiers import normalise_column_key, normalise_table_key
from .layers import CheckResult, Layer, allow, internal_error, refuse
from .policy import DEFAULT_DIALECT, GovernancePolicy
from .scopes import ScopeView, iter_scopes

__all__ = ["check", "GovernanceUsageError", "graded_delivery_eligible"]


class GovernanceUsageError(TypeError):
    """A security parameter was not wired up. Not a fault of the statement."""


#: Writes, DDL, transaction control, or a statement sqlglot does not model (``exp.Command``).
#: A denylist inside a fail-closed frame: the root must already be a read (``READ_ROOTS``),
#: so these catch writes hidden inside one, such as ``WITH ... DELETE ... RETURNING``.
WRITE_NODES: tuple[type[exp.Expr], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Copy,
    exp.Create, exp.Drop, exp.Alter, exp.TruncateTable,
    exp.Grant, exp.Revoke,
    exp.Transaction, exp.Commit, exp.Rollback,
    exp.Set, exp.SetItem, exp.Use, exp.Analyze, exp.Attach, exp.Detach,
    exp.Refresh, exp.Cache, exp.Uncache, exp.Pragma, exp.Kill, exp.Command,
    exp.Export, exp.Put, exp.Get,
)

#: Roots that are reads. ``WITH ... SELECT`` parses as a ``Select`` carrying its CTEs.
READ_ROOTS: tuple[type[exp.Expr], ...] = (exp.Select, exp.SetOperation, exp.Subquery)


def check(
    sql: str,
    *,
    retrieved_tables: frozenset[str] | None,
    corpus: AnalystCorpus | None,
    default_schema: str | None = None,
    dialect: str = DEFAULT_DIALECT,
    policy: GovernancePolicy | None = None,
) -> CheckResult:
    """Column authorization comes from ``corpus`` alone, so a second set cannot drift. See ADR 0006."""
    if retrieved_tables is None or isinstance(retrieved_tables, (str, bytes)):
        raise GovernanceUsageError(
            "retrieved_tables must be a set of table keys. None is not 'no restriction' and a "
            "string is not a set of one; v1's `if allowlist is not None` fell through to "
            "gateway.execute, and a blocked result here would record 'this query was "
            "unsafe' for what is really 'the authorization argument was never wired up'."
        )
    if corpus is None or not isinstance(corpus, AnalystCorpus):
        raise GovernanceUsageError(
            "corpus must be an AnalystCorpus. Passing column sets beside an unfiltered "
            "corpus was B10: two definitions of 'excluded' that drifted. "
            "corpus.for_analyst(...) is the single boundary."
        )
    policy = policy or GovernancePolicy()

    # Outside the try: a malformed key is a caller error, not an unsafe query.
    retrieved_tables_keys = frozenset(normalise_table_key(key, default_schema) for key in retrieved_tables)
    excluded_keys = frozenset(
        normalise_column_key(key, default_schema) for key in corpus.excluded_columns
    )
    suspect_keys = frozenset(
        normalise_column_key(key, default_schema) for key in corpus.suspect_columns
    )
    allowed_keys = frozenset(
        normalise_column_key(key, default_schema) for key in corpus.allowed_columns
    )
    evaluated: list[Layer] = []
    layer = Layer.PARSE
    try:
        parsed = _parse(sql, dialect, evaluated)
        if isinstance(parsed, dict):
            return parsed
        tree = parsed

        layer = Layer.NO_WRITE
        evaluated.append(layer)
        blocked = _no_write(tree, evaluated)
        if blocked is not None:
            return blocked

        views = iter_scopes(tree)

        layer = Layer.FUNCTIONS
        evaluated.append(layer)
        blocked = _functions(views, policy, evaluated)
        if blocked is not None:
            return blocked

        layer = Layer.BINDING
        evaluated.append(layer)
        # Every declared column key, any disposition. Binding picks a source and authorises
        # nothing. An excluded column must still bind, or the failure is "ambiguous" and
        # the column layer never refuses it.
        declared = excluded_keys | suspect_keys | allowed_keys
        bound = bind(views, default_schema=default_schema, known_columns=declared)
        if isinstance(bound, LayerRefusal):
            return refuse(bound.rule_id, bound.detail, evaluated=evaluated)

        layer = Layer.COLUMNS
        evaluated.append(layer)
        blocked = _columns(bound, allowed_keys, excluded_keys, suspect_keys, policy, evaluated)
        if blocked is not None:
            return blocked

        layer = Layer.TABLES
        evaluated.append(layer)
        blocked = _tables(bound, retrieved_tables_keys, evaluated)
        if blocked is not None:
            return blocked

        return allow(evaluated=evaluated, bound=bound.as_bound())
    except Exception as err:  # noqa: BLE001 - the point is that nothing escapes
        return internal_error(
            layer,
            f"{type(err).__name__} inside the {layer.name} layer: {err}",
            evaluated=evaluated,
        )


def _parse(sql: str, dialect: str, evaluated: list[Layer]) -> exp.Expr | CheckResult:
    evaluated.append(Layer.PARSE)
    try:
        statements = [statement for statement in sqlglot.parse(sql, dialect=dialect) if statement]
    except SqlglotError as err:
        return refuse("r_unparseable", f"{type(err).__name__}: {err}", evaluated=evaluated)
    if not statements:
        return refuse("r_empty_statement", "no statement to check", evaluated=evaluated)
    if len(statements) > 1:
        return refuse(
            "r_multiple_statements",
            f"{len(statements)} statements; only the first would ever be checked",
            evaluated=evaluated,
        )
    return statements[0]


def _no_write(tree: exp.Expr, evaluated: list[Layer]) -> CheckResult | None:
    if not isinstance(tree, READ_ROOTS):
        return refuse(
            "r_not_a_read",
            f"the statement root is {type(tree).__name__}, not a read expression",
            evaluated=evaluated,
        )
    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            return refuse(
                "r_write_construct",
                f"{type(node).__name__} inside a read statement",
                evaluated=evaluated,
            )
        if isinstance(node, exp.Into):
            return refuse("r_select_into", "SELECT ... INTO creates a relation", evaluated=evaluated)
        if isinstance(node, exp.Lock):
            return refuse(
                "r_locking_clause",
                "a locking clause takes row locks, which a read-only session must not",
                evaluated=evaluated,
            )
    return None


def _nearest_function(node: exp.Expr) -> exp.Func | None:
    """Innermost function this node is an argument of, so a nested call is not this one's."""
    current = node.parent
    while current is not None:
        if isinstance(current, exp.Func):
            return current
        current = current.parent
    return None


def _scope_arguments(func: exp.Func, own: frozenset[int]) -> Iterator[exp.Column | exp.Star]:
    """Own scope only, so ``NULLIF(COUNT(*), 0)`` keeps the ``count(*)`` carve-out."""
    for node in func.find_all(exp.Column, exp.Star):
        # isinstance, not find_all's filter alone: the caller reads .parts and .table.
        if not (id(node) in own and isinstance(node, (exp.Column, exp.Star))):
            continue
        if _nearest_function(node) is not func:
            continue
        yield node


def _functions(
    views: Sequence[ScopeView], policy: GovernancePolicy, evaluated: list[Layer]
) -> CheckResult | None:
    """The allowlist, plus the whole-row argument rule (B2)."""
    for view in views:
        own = frozenset(id(node) for node in view.nodes)
        for func in view.functions():
            name = canonical_function_name(func)
            if name not in policy.permitted_functions:
                return refuse(
                    "r_function_not_permitted",
                    f"{name} is not on the positive allowlist",
                    evaluated=evaluated,
                )
            # A bare star is ``exp.Star``; ``c.*`` is a ``Column`` whose ``this`` is a Star.
            # ``count(*)`` is carved out only when the Star is the Count's own argument, so
            # ``count(DISTINCT *)`` — the Star behind Distinct — still refuses.
            for node in _scope_arguments(func, own):
                if isinstance(node, exp.Star):
                    # The Star must be the Count's own argument. One node further down is a whole row.
                    if isinstance(func, exp.Count) and func.this is node:
                        continue
                    return refuse(
                        "r_whole_row_argument",
                        f"{name}(*) emits every column of the row; count(*) is the only "
                        "carve-out",
                        evaluated=evaluated,
                    )
                if isinstance(node.this, exp.Star):
                    return refuse(
                        "r_whole_row_argument",
                        f"{name}({node.sql()}) emits every column of {node.table} — "
                        "including excluded and suspect ones — with zero Column nodes "
                        "for them (B2)",
                        evaluated=evaluated,
                    )
    return None


def _columns(
    bound: Bindings,
    allowed: frozenset[str],
    excluded: frozenset[str],
    suspect: frozenset[str],
    policy: GovernancePolicy,
    evaluated: list[Layer],
) -> CheckResult | None:
    """Excluded, then suspect, then not allowed. The first match wins."""
    for binding in bound.columns:
        if binding.column_key in excluded:
            return refuse(
                "r_column_excluded",
                f"{binding.reference} binds to {binding.column_key}, which is excluded",
                evaluated=evaluated,
            )
        if binding.column_key in suspect and policy.hard_block_suspect:
            return refuse(
                "r_column_suspect",
                f"{binding.reference} binds to {binding.column_key}, which is suspect, "
                "and hard_block_suspect is on",
                evaluated=evaluated,
            )
        if binding.column_key not in allowed:
            return refuse(
                "r_column_not_allowed",
                f"{binding.reference} binds to {binding.column_key}, which is not allowed",
                evaluated=evaluated,
            )
    return None


def _tables(
    bound: Bindings,
    retrieved_tables: frozenset[str],
    evaluated: list[Layer],
) -> CheckResult | None:
    """A base table must be in this turn's retrieved set."""
    for reference, key in bound.tables.items():
        if key not in retrieved_tables:
            return refuse(
                "r_table_not_retrieved",
                f"{reference} resolves to {key}, which this turn does not license",
                evaluated=evaluated,
            )
    return None


def graded_delivery_eligible(result: CheckResult, policy: GovernancePolicy | None = None) -> bool:
    """No result is eligible. This function does not choose graded delivery."""
    del result, policy
    return False


def _assert_no_write_frame_is_closed() -> None:
    """An overlap would be a root that is both a legal read and a write, so the denylist frame would never see it."""
    overlap = [cls for cls in READ_ROOTS if cls in WRITE_NODES]
    if overlap:  # pragma: no cover - import-time guard
        raise AssertionError(f"read roots that are also write nodes: {overlap}")


_assert_no_write_frame_is_closed()
