"""Five-layer SQL guardrails (Server step 8; Architecture section 6).

Ordered, fail-closed on any layer:

1. **syntax** parses as valid SQL (sqlglot).
2. **policy blacklist** no DDL/DML/PRAGMA/etc.; read-only single statement only.
3. **AST column allowlist** every referenced column is a known, non-excluded,
   non-``suspect`` column (dev/BIRD hard-blocks suspect; prod soft-warns).
4. **term-semantics** referenced assets match the bound terms.
5. **cost / EXPLAIN** estimated cost under budget.

These run in the server's ``wrap_tool_call`` middleware. The refuse-gate (D5)
runs *concurrently*, not as a sixth layer.

Build status: L1 to L3 are enforced. L4 to L5 are not yet wired into ``check``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

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
    """L1: parse. Returns the verdict and, on success, the parsed statements."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as err:
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


def _source_map(root: exp.Expression) -> tuple[dict[str, str | None], bool]:
    """Map each in-scope source name to its physical base table, or ``None`` if it
    is a derived source (CTE / subquery). Returns the map and whether any derived
    source is present.

    Both the physical name and any alias key the same source, so a column may be
    written either ``customers.col`` or ``c.col``. The view is flattened across
    scopes; that is conservative for an allow/block decision and never lets an
    unknown physical column through.
    """
    cte_names = {cte.alias for cte in root.find_all(exp.CTE) if cte.alias}
    sources: dict[str, str | None] = {}
    has_derived = False

    for table in root.find_all(exp.Table):
        physical = table.name
        derived = physical in cte_names
        has_derived = has_derived or derived
        for key in filter(None, (physical, table.alias)):
            sources[key] = None if derived else physical

    for cte in root.find_all(exp.CTE):
        if cte.alias:
            sources[cte.alias] = None
            has_derived = True
    for sub in root.find_all(exp.Subquery):
        if sub.alias:
            sources[sub.alias] = None
            has_derived = True

    return sources, has_derived


def _layer_columns(
    root: exp.Expression,
    allowed: set[str],
    suspect: set[str],
    hard_block_suspect: bool,
) -> GuardrailVerdict:
    """L3: every column resolves to an allowed physical column.

    Columns from CTEs/subqueries are deferred to the SELECT that defines them
    (whose own base-column references are checked here). A ``suspect`` column is
    hard-blocked when ``hard_block_suspect`` (dev/BIRD) and permitted otherwise.
    """
    layer = GuardrailLayer.ast_column_allowlist
    sources, has_derived = _source_map(root)
    allowed_bare = {ref.split(".", 1)[1] for ref in allowed}
    suspect_bare = {ref.split(".", 1)[1] for ref in suspect}

    for column in root.find_all(exp.Column):
        name = column.name
        table_ref = column.table

        if table_ref:
            if table_ref not in sources:
                return _fail(layer, f"column references unknown source '{table_ref}'")
            physical = sources[table_ref]
            if physical is None:
                continue  # derived source; validated where it is defined
            ref = f"{physical}.{name}"
            if hard_block_suspect and ref in suspect:
                return _fail(layer, f"suspect (decoy) column blocked: {ref}")
            if ref in allowed or ref in suspect:
                continue
            return _fail(layer, f"column not in the allowlist: {ref}")

        # Bare column: attribute by name (scope-flattened).
        if hard_block_suspect and name in suspect_bare and name not in allowed_bare:
            return _fail(layer, f"suspect (decoy) column blocked: {name}")
        if name in allowed_bare or name in suspect_bare:
            continue
        if has_derived:
            continue  # may originate in a derived source; defer
        return _fail(layer, f"column not in the allowlist: {name}")

    return _pass()


def check(
    sql: str,
    *,
    allowed_columns: set[str],
    hard_block_suspect: bool,
    suspect_columns: frozenset[str] = frozenset(),
    dialect: str | None = None,
) -> GuardrailVerdict:
    """Run the layers in order; return on the first failure (fail-closed).

    ``allowed_columns`` / ``suspect_columns`` are physical ``table.column``
    references (build them with :func:`column_allowlist`). ``hard_block_suspect``
    is the dev/prod suspect toggle. ``dialect`` is the sqlglot dialect name
    (e.g. ``"sqlite"``) for parsing. L4 to L5 are not yet enforced.
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

    # L4 (term-semantics) and L5 (cost/EXPLAIN) land in later milestones.
    return _pass()
