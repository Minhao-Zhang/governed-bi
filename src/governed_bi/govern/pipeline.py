"""Fixed order. See ADR 0006. Normalise only after the control-character check. Canonicalise before ``check()``. The row
limit is the only later rewrite, and the ledger hashes that string."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Mapping

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.scope import Scope

from ..corpus.analyst import AnalystCorpus
from .check import GovernanceUsageError, check
from .guard import has_control_characters
from .identifiers import fold, fold_map
from .layers import CheckResult, refuse
from .policy import DEFAULT_DIALECT, GovernancePolicy
from .scopes import iter_scopes

__all__ = [
    "Prepared",
    "normalise",
    "canonicalise",
    "apply_row_limit",
    "prepare",
    "spellings_for",
]


@dataclass(frozen=True, slots=True)
class Prepared:
    """``sql`` is ``None`` when nothing may run."""

    result: CheckResult
    #: The exact string to hand to ``Connector.execute``, limit included.
    sql: str | None
    raw: str
    canonical: str | None


def normalise(text: str) -> str:
    """NFKC rewrites text, so it runs only after the control-character check. It does not strip those characters."""
    return unicodedata.normalize("NFKC", text)


def spellings_for(
    corpus: AnalystCorpus, retrieved_tables: frozenset[str]
) -> tuple[Mapping[str, str], frozenset[str], Mapping[str, Mapping[str, str]]]:
    """Per table, so one collision is not inherited. A shared bare key is poisoned with ``None``."""
    names: list[str] = []
    # ``None`` is the poison written when a bare key is ambiguous. The return filter drops it.
    by_table: dict[str, Mapping[str, str] | None] = {}
    for table_id in sorted(retrieved_tables):
        table = corpus.get(table_id)
        if table is None:
            continue
        for attr in ("physical_name", "schema"):
            value = getattr(table, attr, None)
            if isinstance(value, str) and value:
                names.append(value)
        own: list[str] = []
        for column_id in getattr(table, "columns", ()) or ():
            column = corpus.get(str(column_id))
            physical = getattr(column, "physical_name", None)
            if isinstance(physical, str) and physical:
                names.append(physical)
                own.append(physical)
        # Bare and schema-qualified: a reference may carry either.
        own_spellings, own_ambiguous = fold_map(own)
        physical_name = getattr(table, "physical_name", None)
        schema = getattr(table, "schema", None)
        if isinstance(physical_name, str) and physical_name:
            bare = fold(physical_name)
            # Poison before the own-collision guard. A self-colliding table must not leave the
            # bare name owned by another schema. ``None`` poisons it; the qualified key still
            # resolves when the collision is not inside that one table.
            by_table[bare] = None if own_ambiguous or bare in by_table else own_spellings
            # A self-colliding table still withholds its qualified key: that collision is real.
            if isinstance(schema, str) and schema and not own_ambiguous:
                by_table[f"{fold(schema)}.{bare}"] = own_spellings
    return (*fold_map(names), {k: v for k, v in by_table.items() if v is not None})


def _handles_in_scope(view) -> dict[str, str | None]:
    """Alias only, from ``selected_sources``. ``None`` is a derived source, so this agrees with ``bind()``."""
    local: dict[str, str | None] = {}
    try:
        selected = view.scope.selected_sources.items()
    except OptimizeError:
        # Two sources answer to one name, so this scope contributes no qualified rewrites.
        # Columns fall through to the flat pass and then to BINDING, which refuses the same
        # shape. Fail closed, and stay in agreement with ``bind()``.
        return {}
    for alias, (_node, source) in selected:
        if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
            name = fold(str(source.name))
            if not name:
                continue
            handle = fold(str(alias)) if alias else name
            local[handle] = f"{fold(str(source.db))}.{name}" if source.db else name
        elif alias:
            local[fold(str(alias))] = None
    return local


def _column_sources(tree: exp.Expr) -> dict[int, str]:
    """This scope, then ancestors, as ``bind()`` does. A whole-tree map rewrote shadowed aliases and still passed;
    dropping every derived handle tree-wide refused an unambiguous name."""
    views = iter_scopes(tree)
    if not views:
        return {}
    per_scope = {id(view.scope): _handles_in_scope(view) for view in views}

    out: dict[int, str] = {}
    for view in views:
        for column in view.columns():
            handle = fold(str(column.table or ""))
            if not handle:
                continue
            # This scope, then ancestors: a correlated reference resolves in a named ancestor.
            scope: Scope | None = view.scope
            while scope is not None:
                local = per_scope.get(id(scope))
                if local is not None and handle in local:
                    key = local[handle]
                    if key is not None:
                        out[id(column)] = key
                    break
                scope = scope.parent
    return out


def canonicalise(
    sql: str,
    *,
    spellings: Mapping[str, str],
    ambiguous: frozenset[str] = frozenset(),
    dialect: str = DEFAULT_DIALECT,
    by_table: Mapping[str, Mapping[str, str]] | None = None,
) -> str | CheckResult:
    """Quote every declared name, including one already spelled correctly. An unquoted identifier is folded by the
    engine after ``check()`` has passed. See ADR 0008."""
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except SqlglotError as err:
        return refuse("r_unparseable", f"{type(err).__name__}: {err}")
    if tree is None:
        return refuse("r_empty_statement", "no statement to canonicalise")

    # Qualified columns first. A qualifier names one table, so a collision in the flat map
    # must not refuse it. The flat map exists because many retrieved tables share one namespace.
    settled: set[int] = set()
    if by_table is None:
        by_table = {}
    if by_table:
        sources = _column_sources(tree)
        for column in tree.find_all(exp.Column):
            identifier = column.this
            if not isinstance(identifier, exp.Identifier):
                continue
            own = by_table.get(sources.get(id(column), ""))
            if own is None:
                continue
            declared = own.get(fold(identifier.name))
            if declared is None:
                # Not this table's column. The flat pass, then BINDING, decides it.
                continue
            identifier.set("this", declared)
            identifier.set("quoted", True)
            settled.add(id(identifier))

    for identifier in tree.find_all(exp.Identifier):
        if id(identifier) in settled:
            continue
        key = fold(identifier.name)
        if key in ambiguous:
            return refuse(
                "r_ambiguous_fold",
                f"{identifier.name} folds onto more than one declared identifier and carries "
                "no qualifier that names which, so the engine would pick one and the column "
                "layer would approve another",
            )
        declared = spellings.get(key)
        if declared is None:
            # Alias, CTE, or output label. Left unquoted: this map does not declare it.
            continue
        if declared != identifier.name:
            identifier.set("this", declared)
        identifier.set("quoted", True)
    return tree.sql(dialect=dialect)


def _existing_limit(tree: exp.Query) -> int | None:
    """``None`` means replace: no limit, a non-numeric limit, or an unrecognised shape. Leaving those skips the cap."""
    node = tree.args.get("limit")
    if node is None:
        return None
    for candidate in (node.args.get("expression"), node.args.get("count")):
        if isinstance(candidate, exp.Literal) and not candidate.is_string:
            try:
                return int(candidate.this)
            except (TypeError, ValueError):
                return None
    return None


def apply_row_limit(sql: str, *, max_rows: int, dialect: str = DEFAULT_DIALECT) -> str | CheckResult:
    """``min(existing, max_rows + 1)`` at the root. A missing parse refuses rather than skipping the limit."""
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except SqlglotError as err:
        return refuse(
            "r_unparseable",
            f"cannot inject a row limit into a statement that does not parse: "
            f"{type(err).__name__}: {err}",
        )
    if tree is None:
        return refuse("r_empty_statement", "no statement to limit")
    if not isinstance(tree, exp.Query):
        # Only a query has a root LIMIT. Anything here cleared check(), so this is unreachable.
        # Refuse rather than return the statement with no limit.
        return refuse(
            "r_not_a_read", f"{type(tree).__name__} has no statement-level row limit"
        )
    ceiling = max_rows + 1
    existing = _existing_limit(tree)
    limit = ceiling if existing is None else min(existing, ceiling)
    return tree.limit(limit).sql(dialect=dialect)


def prepare(
    sql: str,
    *,
    retrieved_tables: frozenset[str] | None,
    corpus: AnalystCorpus | None,
    spellings: Mapping[str, str] | None,
    ambiguous_folds: frozenset[str] = frozenset(),
    spellings_by_table: Mapping[str, Mapping[str, str]] | None = None,
    default_schema: str | None = None,
    dialect: str = DEFAULT_DIALECT,
    policy: GovernancePolicy | None = None,
) -> Prepared:
    """``spellings`` has no default: an optional control ships unwired. Pass ``{}`` to declare none. See ADR 0008."""
    if spellings is None:
        raise GovernanceUsageError(
            "prepare() requires `spellings`. Build it with `spellings_for(corpus, "
            "retrieved_tables)`, or pass `spellings={}` to state that this call declares none. "
            "It has no default because it had one: the only production caller omitted "
            "it, so every mixed-case identifier in the corpus reached the engine folded "
            "to lower case and failed after a passing result (ADR 0008 P1)."
        )
    policy = policy or GovernancePolicy()

    if has_control_characters(sql):
        return Prepared(
            result=refuse(
                "r_control_characters",
                "the statement contains control, bidi or zero-width characters, which "
                "NFKC would fold into ordinary text",
            ),
            sql=None,
            raw=sql,
            canonical=None,
        )

    normalised = normalise(sql)
    canonical = canonicalise(
        normalised,
        spellings=spellings or {},
        ambiguous=ambiguous_folds,
        dialect=dialect,
        by_table=spellings_by_table,
    )
    if isinstance(canonical, dict):
        return Prepared(result=canonical, sql=None, raw=sql, canonical=None)

    result = check(
        canonical,
        retrieved_tables=retrieved_tables,
        corpus=corpus,
        default_schema=default_schema,
        dialect=dialect,
        policy=policy,
    )
    if not result["passed"]:
        return Prepared(result=result, sql=None, raw=sql, canonical=canonical)

    limited = apply_row_limit(canonical, max_rows=policy.max_rows, dialect=dialect)
    if isinstance(limited, dict):
        return Prepared(result=limited, sql=None, raw=sql, canonical=canonical)
    return Prepared(result=result, sql=limited, raw=sql, canonical=canonical)
