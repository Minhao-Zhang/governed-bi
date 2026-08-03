"""``check()``: the seven layers, in order, first failure wins (ADR 0006 §1).

Everything about this function's shape is a v1 incident.

**Every security parameter is required (G1).** ``licensed`` has no default and
``licensed=None`` **raises**. v1 wrapped its pre-execute recheck in
``if allowlist is not None`` and fell through to ``gateway.execute`` when the
argument was missing — the guard added to make the path defence-in-depth had removed
the only authorization on it. And an *empty* set licenses nothing; it is not "no
restriction".

**A missing argument raises rather than returning a blocked verdict.**
``licensed=None`` is a caller error, not a fact about the SQL. A verdict reading
"blocked at the TABLES layer" would be recorded as *this query was unsafe* when the
truth is *the authorization argument was never wired up* — two different incidents,
and collapsing them is B10's shape.

**Column authorization comes from an** ``AnalystCorpus`` **(ADR 0005 §1.5).**
Passing parallel column sets beside an unfiltered corpus was B10. A missing
``corpus`` raises :class:`GovernanceUsageError` — a caller error, not a statement
fault.

**Any exception is ``passed=False`` — and is counted.** ``RecursionError`` from
pathological nesting and tokenizer errors from unterminated literals both escaped
v1's parse layer. But a swallowed exception that is not *counted* is worse than a
crash: ADR 0006 §12 records the exact chain, in which a ``NameError`` in the
function-layer walk turns every turn in an arm into a refusal, ``crash_rate == 0``,
every register key present, run declared quotable. So the wrapper's verdict carries
:data:`~governed_bi.govern.layers.GUARDRAIL_ERROR`, and
``ExecutionRecord.guardrail_errors`` counts it (see :mod:`.ledger`).

**A parse failure is not a guardrail error.** It is a fact about the statement, so
it refuses at PARSE with a rule id. Conflating the two would make a model that
writes bad SQL look like a broken governance layer, and hide a broken governance
layer among models that write bad SQL.
"""

from __future__ import annotations

from typing import Iterator, Sequence

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

from ..corpus.analyst import AnalystCorpus
from ..register.knobs import Unset
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
#: This is a **denylist inside a fail-closed frame**, and the frame is what makes it
#: safe: the root of the statement must already be a read expression, so these are
#: the constructs that can hide *inside* one — ``WITH d AS (DELETE FROM t RETURNING
#: *) SELECT * FROM d`` is a ``Select`` at the root and deletes rows.
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

    # Normalised OUTSIDE the wrapper: a malformed key is a caller error, and turning
    # it into a blocked verdict would hide a broken caller as an unsafe query.
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
        # Every column key the corpus declares, whatever its disposition. Binding uses
        # it to decide *which* source a bare name belongs to and authorises nothing
        # with it — an excluded column must still bind, or the column layer never gets
        # to refuse it and the statement fails as "ambiguous" instead of "excluded".
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
        blocked = _tables(bound, licensed_keys, evaluated)
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
    """Argument nodes of ``func`` **directly**, in its own scope.

    Two filters, and both were measured rather than reasoned about.

    ``own`` excludes nested scopes: ``exp.Exists`` is a function whose argument is a
    whole subquery, so an unfiltered ``find_all`` would test an inner scope's column
    names against an outer scope's table aliases — a false refusal that would look like
    a governance bug and be "fixed" by loosening the rule.

    :func:`_nearest_function` excludes *nested functions*, and without it the star in
    ``NULLIF(COUNT(*), 0)`` belongs to ``NULLIF`` as well as to ``COUNT``, so the
    ``count(*)`` carve-out does not apply on the outer visit and the statement refuses.
    That shape appears in **60 of the 6,743 gold statements** (measured 2026-08-03), so
    the bug was a 0.9% false-refusal rate on gold that no unit test would have found.
    """
    for node in func.find_all(exp.Column, exp.Star):
        # isinstance rather than trusting find_all's filter: the narrowing is what lets
        # the caller read `.parts` and `.table` without a cast, and a shape that is
        # neither is skipped rather than read with the wrong attribute.
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
            for node in _scope_arguments(func, own):
                if isinstance(node, exp.Star):
                    # count(*) exactly. Every other star argument is a whole row.
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
) -> CheckVerdict | None:
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


def _tables(bound: Bindings, licensed: frozenset[str], evaluated: list[Layer]) -> CheckVerdict | None:
    for reference, key in bound.tables.items():
        if key not in licensed:
            return refuse(
                "r_table_not_licensed",
                f"{reference} resolves to {key}, which this turn does not license",
                evaluated=evaluated,
            )
    return None


def shape_estimate(tree: exp.Expr) -> int:
    """A crude, deterministic shape score: base tables + joins + set operations.

    **Deliberately not a cost model.** ADR 0006 OQ2 asks whether the cost layer earns
    its place at all — v1's has no recorded instance of blocking something the other
    layers would have missed — and ``cost_budget`` ships ``UNSET``, so this never runs
    unless an operator sets a bound. A real estimate needs table statistics, which is
    a datasource capability, not a string-level one. Naming it *shape* rather than
    *cost* keeps that honest.
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
    """Whether this verdict may be executed and delivered marked *unverified* (§5).

    **Only ``COST``.** ADR 0006's first draft wrote ``{TABLES, COST}``, copied from
    v1's *entry* set without noticing that v1's **recheck** forgives ``COST`` only,
    with the comment *"an L4 failure means unauthorized base tables and must
    refuse"*. And the redefinition made it worse: v1's table-ish layer was a curated
    *semantic* check, while v2's ``TABLES`` is pure authorization — so §5's own
    argument for excluding the column layer ("it is a confidentiality control, not a
    semantic one") applies verbatim. Under the first draft's rule, a pooled
    multi-schema deployment would execute SQL against unlicensed tables and show the
    analyst the rows.

    Reaching ``COST`` is a proof minted by ``check()`` that the six layers below it
    passed. **Everything else hard-refuses**, including every entry that never earned
    a verdict — cap, error, exhausted, no-coverage and missing-pass-result all carry
    ``failed_layer=None``, and treating that as forgivable was B3. So a passing
    verdict is *not* eligible either: it does not need to be, and a predicate that
    said yes to ``None`` would be the same function v1 shipped.
    """
    policy = policy or GovernancePolicy()
    if not policy.graded_delivery_enabled:
        return False
    return verdict["failed_layer"] is Layer.COST


def _assert_no_write_frame_is_closed() -> None:
    """Import-time guard: the write denylist cannot be read as the whole control.

    ``READ_ROOTS`` and ``WRITE_NODES`` must not overlap. An overlap would mean a
    statement root that is simultaneously a legal read and a write construct, which
    would make the frame that keeps the denylist honest vacuous.
    """
    overlap = [cls for cls in READ_ROOTS if cls in WRITE_NODES]
    if overlap:  # pragma: no cover - import-time guard
        raise AssertionError(f"read roots that are also write nodes: {overlap}")


_assert_no_write_frame_is_closed()
