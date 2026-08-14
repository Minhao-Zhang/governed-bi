"""``check()``: seven layers in order, first failure wins (ADR 0006 §1).

Invariants (G1 / ADR 0006 §12):

* Every security parameter is required; ``licensed=None`` / missing ``corpus``
  raise :class:`GovernanceUsageError` (caller error, not a statement fault).
* An empty ``licensed`` set licenses nothing.
* Column authorization is an :class:`~governed_bi.corpus.analyst.AnalystCorpus`.
* Any exception inside the walk is ``passed=False`` with
  :data:`~governed_bi.govern.layers.GUARDRAIL_ERROR` (counted by the ledger).
* A parse failure refuses at PARSE with a rule id — not a guardrail error.
"""

from __future__ import annotations

from typing import Iterator, Sequence

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

from ..corpus.analyst import AnalystCorpus
from ..register.knobs import Unset
from .access import ResolvedGrant, resolve_grant
from .binding import Bindings, LayerRefusal, bind
from .functions import canonical_function_name
from .identifiers import normalise_column_key, normalise_table_key
from .layers import CheckVerdict, Layer, allow, internal_error, refuse
from .policy import DEFAULT_DIALECT, GovernancePolicy
from .scopes import ScopeView, iter_scopes

__all__ = ["check", "GovernanceUsageError", "graded_delivery_eligible", "shape_estimate"]


class GovernanceUsageError(TypeError):
    """A security parameter was not wired up. Never a statement's fault."""


#: Node types that write, change schema, control transactions, or hand sqlglot a
#: statement it does not model (``exp.Command`` — ``VACUUM``, ``COPY``, anything the
#: parser passes through as text).
#:
#: A **denylist inside a fail-closed frame**: the root must already be a read
#: expression (``READ_ROOTS``), so these are only the constructs that hide *inside*
#: one — ``WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d`` is a ``Select`` at
#: the root and deletes rows.
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
    licensed: frozenset[str] | None,
    corpus: AnalystCorpus | None,
    default_schema: str | None = None,
    dialect: str = DEFAULT_DIALECT,
    policy: GovernancePolicy | None = None,
) -> CheckVerdict:
    """Run the layer stack against ``sql``. Returns on the first failure.

    ``licensed`` is table keys. Column authorization is derived from ``corpus``
    (ADR 0005 §1.5 / ADR 0006 §1) — never from a parallel set that can drift.
    """
    if licensed is None or isinstance(licensed, (str, bytes)):
        raise GovernanceUsageError(
            "licensed must be a set of table keys. None is not 'no restriction' and a "
            "string is not a set of one; v1's `if allowlist is not None` fell through to "
            "gateway.execute, and a blocked verdict here would record 'this query was "
            "unsafe' for what is really 'the authorization argument was never wired up'."
        )
    if corpus is None or not isinstance(corpus, AnalystCorpus):
        raise GovernanceUsageError(
            "corpus must be an AnalystCorpus. Passing column sets beside an unfiltered "
            "corpus was B10: two definitions of 'excluded' that drifted. "
            "corpus.for_analyst(...) is the single boundary."
        )
    policy = policy or GovernancePolicy()

    # Normalised OUTSIDE the try: a malformed key is a caller error, and a blocked
    # verdict would report a broken caller as an unsafe query.
    licensed_keys = frozenset(normalise_table_key(key, default_schema) for key in licensed)
    excluded_keys = frozenset(
        normalise_column_key(key, default_schema) for key in corpus.excluded_columns
    )
    suspect_keys = frozenset(
        normalise_column_key(key, default_schema) for key in corpus.suspect_columns
    )
    allowed_keys = frozenset(
        normalise_column_key(key, default_schema) for key in corpus.allowed_columns
    )
    # ADR 0012. Resolved here, beside the other four, and for the same reason: a policy file
    # with a malformed key is a caller error, and a blocked verdict would report it as an
    # unsafe query. The default grant is open, so every predicate below is constant and the
    # three authorization branches are unreachable — which is the whole of the claim that
    # this seam changed no measured number.
    grant = resolve_grant(policy.access_grant, default_schema)

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
        # Every column key the corpus declares, whatever its disposition: binding uses
        # it to pick *which* source a bare name belongs to and authorises nothing. An
        # excluded column must still bind, or the column layer never gets to refuse it
        # and the statement fails as "ambiguous" instead of "excluded".
        declared = excluded_keys | suspect_keys | allowed_keys
        bound = bind(views, default_schema=default_schema, known_columns=declared)
        if isinstance(bound, LayerRefusal):
            return refuse(bound.rule_id, bound.detail, evaluated=evaluated)

        layer = Layer.COLUMNS
        evaluated.append(layer)
        blocked = _columns(
            bound, allowed_keys, excluded_keys, suspect_keys, grant, policy, evaluated
        )
        if blocked is not None:
            return blocked

        layer = Layer.TABLES
        evaluated.append(layer)
        blocked = _tables(bound, licensed_keys, grant, evaluated)
        if blocked is not None:
            return blocked

        if policy.cost_layer_enabled():
            layer = Layer.COST
            evaluated.append(layer)
            blocked = _cost(tree, policy, evaluated)
            if blocked is not None:
                return blocked

        return allow(evaluated=evaluated, bound=bound.as_bound())
    except Exception as err:  # noqa: BLE001 - the point is that nothing escapes
        return internal_error(
            layer,
            f"{type(err).__name__} inside the {layer.name} layer: {err}",
            evaluated=evaluated,
        )


def _parse(sql: str, dialect: str, evaluated: list[Layer]) -> exp.Expr | CheckVerdict:
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


def _no_write(tree: exp.Expr, evaluated: list[Layer]) -> CheckVerdict | None:
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
    """The innermost function this node is an argument of."""
    current = node.parent
    while current is not None:
        if isinstance(current, exp.Func):
            return current
        current = current.parent
    return None


def _scope_arguments(func: exp.Func, own: frozenset[int]) -> Iterator[exp.Column | exp.Star]:
    """Argument nodes of ``func`` in its own scope.

    ``own`` excludes nested scopes; :func:`_nearest_function` excludes nested
    functions (so ``NULLIF(COUNT(*), 0)`` keeps the ``count(*)`` carve-out).
    """
    for node in func.find_all(exp.Column, exp.Star):
        # isinstance rather than trusting find_all's filter: the narrowing lets the
        # caller read `.parts` and `.table` without a cast.
        if not (id(node) in own and isinstance(node, (exp.Column, exp.Star))):
            continue
        if _nearest_function(node) is not func:
            continue
        yield node


def _functions(
    views: Sequence[ScopeView], policy: GovernancePolicy, evaluated: list[Layer]
) -> CheckVerdict | None:
    """The allowlist, plus the whole-row argument rule that closes B2."""
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
            # Two star shapes, and they are different AST nodes rather than two spellings of
            # one. A **bare** star is an `exp.Star`; a **qualified** one, `c.*`, is an
            # `exp.Column` whose `this` is a Star. Re-instrumented 2026-08-12 over the
            # adversarial suite's 115 cases, counting *executions* — every case runs the stack
            # twice, once through `check()` and once through `prepare()`, so each figure is two
            # per case: the carve-out `continue` below fires 12 (6 cases), the qualified branch
            # 2 (`b2_count_qualified_star`) and the bare branch's *refuse* arm 2
            # (`b2_count_distinct_star`). The bare arm had **no case at all** when the branches
            # were first instrumented, which is what `b2_count_distinct_star` was written to
            # close: `count(DISTINCT *)` parses as `Count(this=Distinct(expressions=[Star()]))`,
            # so `func.this` is the Distinct and the Star reaches the refusal. Dropping
            # `and func.this is node` widens the carve-out to that shape and the whole suite
            # stayed green until that case existed.
            for node in _scope_arguments(func, own):
                if isinstance(node, exp.Star):
                    # count(*) exactly: the Star must be the Count's own argument, so a Star one
                    # node further down (behind DISTINCT) is a whole row like any other.
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
    grant: ResolvedGrant,
    policy: GovernancePolicy,
    evaluated: list[Layer],
) -> CheckVerdict | None:
    """Four rules in a fixed order, and the order is an argument (ADR 0012 §4).

    ``excluded`` and ``suspect`` are corpus-wide facts that precede any principal, so they
    are reported first: they reveal nothing about *this* caller. ``denies_column`` comes
    next, ahead of ``not allowed``, because collapsing "you may not read this" into "there
    is no such column" is exactly the conflation §4.2 asks to end one layer up. It runs
    after the corpus rules and never instead of them — denial narrows an allowlist, it does
    not replace one.
    """
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
        if grant.denies_column(binding.column_key):
            return refuse(
                "r_column_not_authorized",
                f"{binding.reference} binds to {binding.column_key}, which this principal "
                "is denied",
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
    licensed: frozenset[str],
    grant: ResolvedGrant,
    evaluated: list[Layer],
) -> CheckVerdict | None:
    """Licence first, then authorization, then the unenforceable predicate (ADR 0012 §3).

    **The order is the security property, not a style choice.** ``licensed`` is what
    retrieval found this turn; the grant is what the principal may ever see. Asking the
    grant first would make the pair of rules an oracle: a caller could tell a table that
    exists-but-is-denied from one that does not exist, by reading which refusal came back.
    Asking the licence first means ``r_table_not_authorized`` fires only for a table this
    turn already put in front of the model, so it discloses nothing new — and it is then
    exactly the distinction open-work.md §4.2 says the conflated set is costing:
    "retrieval missed" and "you may not" stop being the same reason code.
    """
    for reference, key in bound.tables.items():
        if key not in licensed:
            return refuse(
                "r_table_not_licensed",
                f"{reference} resolves to {key}, which this turn does not license",
                evaluated=evaluated,
            )
        if not grant.authorizes_table(key):
            return refuse(
                "r_table_not_authorized",
                f"{reference} resolves to {key}, which this principal is not authorized to "
                "read. The turn licensed it and the access policy does not grant it",
                evaluated=evaluated,
            )
        if grant.refuses_for_row_predicate(key):
            return refuse(
                "r_row_predicate_unenforced",
                f"{reference} resolves to {key}, which carries a declared row-level "
                "predicate this engine does not apply. Executing the statement would return "
                "the rows the predicate exists to withhold, so it refuses instead; declare "
                "enforcement = \"database_role\" once the database enforces it",
                evaluated=evaluated,
            )
    return None


def shape_estimate(tree: exp.Expr) -> int:
    """Crude shape score: base tables + joins + set operations (not a cost model).

    ``cost_budget`` ships ``UNSET`` (ADR 0006 OQ2); this runs only when set.
    """
    return (
        len(list(tree.find_all(exp.Table)))
        + len(list(tree.find_all(exp.Join)))
        + len(list(tree.find_all(exp.SetOperation)))
    )


def _cost(tree: exp.Expr, policy: GovernancePolicy, evaluated: list[Layer]) -> CheckVerdict | None:
    budget = policy.cost_budget
    assert not isinstance(budget, Unset)  # cost_layer_enabled() is the only caller's gate
    estimate = shape_estimate(tree)
    if estimate > budget:
        return refuse(
            "r_cost_budget_exceeded",
            f"shape estimate {estimate} exceeds the budget {budget}",
            evaluated=evaluated,
        )
    return None


def graded_delivery_eligible(verdict: CheckVerdict, policy: GovernancePolicy | None = None) -> bool:
    """Whether graded delivery may retry (ADR 0006 §5).

    Requires a positively established non-hard failure; never ``failed_layer=None``.
    Only ``COST`` failures are eligible.
    """
    policy = policy or GovernancePolicy()
    if not policy.graded_delivery_enabled:
        return False
    return verdict["failed_layer"] is Layer.COST


def _assert_no_write_frame_is_closed() -> None:
    """Import-time guard: ``READ_ROOTS`` and ``WRITE_NODES`` must not overlap.

    An overlap is a root that is simultaneously a legal read and a write construct,
    which makes the frame that keeps the denylist honest vacuous.
    """
    overlap = [cls for cls in READ_ROOTS if cls in WRITE_NODES]
    if overlap:  # pragma: no cover - import-time guard
        raise AssertionError(f"read roots that are also write nodes: {overlap}")


_assert_no_write_frame_is_closed()
