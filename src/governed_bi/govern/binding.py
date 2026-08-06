"""Binding: one positive rule (ADR 0006 §4).

Every ``Column`` node, every ``USING``/``NATURAL`` join key, and every ``FROM``
source must bind to **exactly one** base source in its own scope (or a named
ancestor for correlated refs). Zero or more than one ⇒ refuse. Allowlist
membership is not binding.

Strictnesses: bare name with multiple base sources refuses; bare name in a mixed
base+derived scope refuses; ``USING (col)`` requires the key in every base source
of the scope. Downstream layers read this binding as their only input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from sqlglot import expressions as exp
from sqlglot.optimizer.scope import Scope

from .identifiers import column_key, fold, table_key
from .scopes import ScopeView

__all__ = ["BoundSource", "ColumnBinding", "LayerRefusal", "Bindings", "bind"]


@dataclass(frozen=True, slots=True)
class BoundSource:
    """One source a reference can bind to."""

    #: How the statement names it: the alias, or the (possibly qualified) table name.
    reference: str
    #: ``"base"`` (a real table) or ``"derived"`` (a subquery or CTE).
    kind: str
    schema: str | None = None
    name: str | None = None
    #: ``{schema}.{physical_name}``, folded. Base sources only.
    key: str | None = None


@dataclass(frozen=True, slots=True)
class ColumnBinding:
    """A column reference and the exactly-one base source it bound to."""

    reference: str
    table_key: str
    #: ``{schema}.{table}.{column}``, folded. The column layer's only input.
    column_key: str


@dataclass(frozen=True, slots=True)
class LayerRefusal:
    """A rule id and its detail. The layer comes from ``layers.RULES``."""

    rule_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class Bindings:
    """Every resolved reference in the statement."""

    #: reference → table key, for the table layer.
    tables: dict[str, str] = field(default_factory=dict)
    columns: tuple[ColumnBinding, ...] = ()
    #: References that bound to something that is **not** a base column: a derived
    #: source, or a projection alias. Recorded so the verdict shows they were seen
    #: and resolved — an unrecorded reference is indistinguishable from one the walk
    #: never reached.
    opaque: dict[str, str] = field(default_factory=dict)

    def as_bound(self) -> dict[str, str]:
        """The verdict's ``bound`` map: every reference → the source it bound to."""
        out = dict(self.tables)
        out.update({binding.reference: binding.table_key for binding in self.columns})
        out.update(self.opaque)
        return out


def _in_order_by(node: exp.Expr, root: exp.Expr) -> bool:
    """Whether ``node`` sits under this scope's ``ORDER BY``.

    The one place a bare name may legitimately mean *a projection alias* rather than
    a column: Postgres resolves ``ORDER BY`` against output names first. ``GROUP BY``
    and ``HAVING`` deliberately do **not** get this treatment — there Postgres
    prefers the *input* column, so treating the name as an alias would skip the
    column check on a reference the engine resolves to a real column.
    """
    current: exp.Expr | None = node
    while current is not None and current is not root:
        if isinstance(current, exp.Order):
            return True
        current = current.parent
    return False


def _lookup(scope: Scope, name: str, sources: Mapping[int, Mapping[str, BoundSource]]) -> BoundSource | None:
    """Resolve a qualifier in ``scope``, then in its ancestors (correlated refs)."""
    current: Scope | None = scope
    while current is not None:
        local = sources.get(id(current))
        if local and name in local:
            return local[name]
        current = current.parent
    return None


def _classify_sources(
    view: ScopeView, default_schema: str | None, tables: dict[str, str]
) -> dict[str, BoundSource]:
    """This scope's sources, keyed on the folded name references use.

    An **aliased** table is registered under its alias only. That is not tidiness:
    Postgres hides the table name behind an alias, so ``public.customers.id`` against
    ``FROM public.customers AS cc`` does not resolve for the engine either, and a
    validator that resolved it would approve a reference the engine will reject —
    then the next reader "fixes" the mismatch by quoting, which is B5's shape.
    """
    local: dict[str, BoundSource] = {}
    for alias, source in view.scope.sources.items():
        if isinstance(source, exp.Table):
            # A table-valued function parses as exp.Table wrapping a Func, so it has
            # no Identifier and no name to license. Recorded as unresolvable rather
            # than refused here; bind() refuses it, at layer 4.
            if not isinstance(source.this, exp.Identifier):
                local[fold(alias) if alias else ""] = BoundSource(alias or "", "unresolvable")
                continue
            schema = source.db or default_schema
            reference = f"{source.db}.{source.name}" if source.db else source.name
            key = table_key(schema, source.name)
            local[fold(alias) if alias else fold(source.name)] = BoundSource(
                reference, "base", schema, source.name, key
            )
            tables[reference] = key
        else:
            local[fold(alias)] = BoundSource(alias, "derived")
    return local


def _structural_refusal(view: ScopeView) -> LayerRefusal | None:
    """The shapes that have no binding at all, whatever the corpus says."""
    for node in view.nodes:
        if isinstance(node, exp.Join) and str(node.args.get("method") or "").upper() == "NATURAL":
            return LayerRefusal(
                "r_natural_join",
                "NATURAL JOIN joins on every common column, which is unenumerable, so "
                "no binding exists for its keys",
            )
        if isinstance(node, (exp.From, exp.Join)):
            source = node.this
            # exp.Values is inline literal data written by the statement's author: it
            # reads no relation, so there is nothing to license, and any column
            # reference inside it is an ordinary Column node in this scope that binds
            # by the same rule as everything else. traverse_scope already models it as
            # a derived source. Refusing it cost 655 of the 6,743 gold statements
            # (9.7%, measured 2026-08-03) for no confidentiality gain.
            ok = isinstance(source, (exp.Subquery, exp.Values)) or (
                isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier)
            )
            if not ok:
                return LayerRefusal(
                    "r_table_function",
                    f"{type(source).__name__} in FROM position produces no table to "
                    "license, so the table layer is blind to it",
                )
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Func):
            return LayerRefusal(
                "r_star_projection",
                "a star projection expands to columns the statement never names, so "
                "the allowlist cannot vouch for them. count(*) is the carve-out and "
                "is handled at the function layer",
            )
    return None


def _bind_columns(
    view: ScopeView,
    local: Mapping[str, BoundSource],
    sources: Mapping[int, Mapping[str, BoundSource]],
    columns: list[ColumnBinding],
    opaque: dict[str, str],
    known_columns: frozenset[str],
) -> LayerRefusal | None:
    bases = [source for source in local.values() if source.kind == "base"]
    derived = [source for source in local.values() if source.kind == "derived"]
    aliases = view.output_aliases()

    for node in view.columns():
        if isinstance(node.this, exp.Star):
            return LayerRefusal(
                "r_star_projection",
                f"{node.sql()} expands to columns the statement never names",
            )
        parts = [part.name for part in node.parts]
        reference = ".".join(parts)

        if len(parts) > 3:
            return LayerRefusal("r_unbound_reference", f"{reference} has more than three parts")

        if len(parts) == 3:
            source = _lookup(view.scope, fold(parts[1]), sources)
            if (
                source is None
                or source.kind != "base"
                or fold(source.schema or "") != fold(parts[0])
            ):
                return LayerRefusal(
                    "r_unbound_reference",
                    f"{reference} names no source in scope. Its key being in a column "
                    "allowlist is not a binding — this is B6",
                )
            columns.append(_binding(reference, source, parts[2]))
            continue

        if len(parts) == 2:
            source = _lookup(view.scope, fold(parts[0]), sources)
            if source is None:
                return LayerRefusal("r_unbound_reference", f"{reference} names no source in scope")
            if source.kind == "base":
                columns.append(_binding(reference, source, parts[1]))
            elif source.kind == "derived":
                opaque[reference] = f"derived:{source.reference}"
            else:
                return LayerRefusal("r_table_function", f"{reference} binds to a table function")
            continue

        name = fold(parts[0])
        whole_row = _whole_row_refusal(view, name, known_columns, bases)
        if whole_row is not None:
            return whole_row
        candidates = [
            source
            for source in bases
            if column_key(source.schema, source.name or "", parts[0]) in known_columns
        ]
        if name in aliases and _in_order_by(node, view.expression):
            opaque[reference] = f"alias:{parts[0]}"
        elif len(bases) == 1 and not derived:
            columns.append(_binding(reference, bases[0], parts[0]))
        elif len(candidates) == 1 and not derived:
            # The corpus says exactly one in-scope base declares this column, so there
            # is exactly one binding — which is the rule, not a relaxation of it. It
            # also agrees with the engine: Postgres itself raises "column reference is
            # ambiguous" when two joined tables declare the name, and the branch below
            # refuses precisely then. Without this, 79 of 6,743 gold statements refuse
            # for an ambiguity the corpus can resolve (measured 2026-08-03).
            columns.append(_binding(reference, candidates[0], parts[0]))
        elif not bases and len(derived) == 1:
            opaque[reference] = f"derived:{derived[0].reference}"
        elif not bases and not derived:
            return LayerRefusal(
                "r_unbound_reference",
                f"{reference} has no source to bind to: this scope selects from nothing",
            )
        else:
            return LayerRefusal(
                "r_ambiguous_reference",
                f"{reference} could bind to {len(bases)} base and {len(derived)} derived "
                "sources. Leftmost-table resolution would pick one, and in an "
                "obfuscated corpus the one it picks can be the decoy",
            )
    return None


def _whole_row_refusal(
    view: ScopeView,
    name: str,
    known_columns: frozenset[str],
    bases: Sequence[BoundSource],
) -> LayerRefusal | None:
    """B2 with no function in sight: a bare name that means *the whole row*.

    In Postgres a bare identifier in an expression is resolved as a **column** of one of
    the FROM items, and means the row as a composite value only when no column of that
    name exists. So ``SELECT avg(price) FROM cars.price`` reads the column, and
    ``SELECT max(t) FROM customers t`` reads every column of the row — including
    excluded and suspect ones — while producing no ``Column`` node for any of them.

    Telling those apart needs the corpus, which is why this lives here and not at the
    function layer: ``known_columns`` says whether an in-scope base declares the name.
    With no corpus knowledge the test cannot run, and the reference falls through to the
    ordinary binding rule and then to the column layer, which has nothing to allow it
    against and refuses — fail-closed either way, with the better reason when the
    information exists.

    **Owed verification.** The precedence claim above is Postgres's documented name
    resolution and has not been re-checked against a live server on this branch; the
    check is one statement (``SELECT avg(price) FROM cars.price`` against the obfuscated
    corpus) and the answer decides whether the corpus-informed branch is a fix or a
    hole. Recorded rather than assumed, because a governance rule resting on an
    unverified engine behaviour is exactly what B5 was.
    """
    if not known_columns or name not in view.source_names:
        return None
    declares = any(
        column_key(source.schema, source.name or "", name) in known_columns for source in bases
    )
    if declares:
        return None
    return LayerRefusal(
        "r_whole_row_reference",
        f"{name} names a source and no in-scope base declares a column of that name, so "
        "it is a whole-row reference: every column of the row, with no Column node for "
        "any of them (B2)",
    )


def _binding(reference: str, source: BoundSource, column: str) -> ColumnBinding:
    assert source.key is not None and source.name is not None  # base sources only
    return ColumnBinding(
        reference=reference,
        table_key=source.key,
        column_key=column_key(source.schema, source.name, column),
    )


def _bind_join_keys(
    view: ScopeView, local: Mapping[str, BoundSource], columns: list[ColumnBinding]
) -> LayerRefusal | None:
    """``USING (col)`` keys, which are not ``Column`` nodes.

    A ``find_all(exp.Column)`` sweep never sees them, so v1's column layer never
    checked a join key — an excluded column was usable as a join key while being
    unusable in a projection.
    """
    bases = [source for source in local.values() if source.kind == "base"]
    derived = [source for source in local.values() if source.kind != "base"]
    for node in view.nodes:
        if not isinstance(node, exp.Join):
            continue
        keys: Sequence[exp.Expression] = node.args.get("using") or []
        if not keys:
            continue
        if derived or len(bases) < 2:
            return LayerRefusal(
                "r_ambiguous_reference",
                "USING requires every joined source to be a base table, or which table "
                "owns the key is unrecoverable",
            )
        for key in keys:
            for source in bases:
                columns.append(_binding(f"{source.reference}.{key.name}", source, key.name))
    return None


def bind(
    views: Iterable[ScopeView],
    *,
    default_schema: str | None,
    known_columns: frozenset[str] = frozenset(),
) -> Bindings | LayerRefusal:
    """Bind every reference in the statement, or refuse.

    Two passes, and the order is forced: ``traverse_scope`` yields children before
    parents, so a correlated reference in an inner scope cannot be resolved until
    every ancestor's sources exist.

    ``known_columns`` is every column key the corpus declares for this turn — allowed,
    excluded and suspect together. It is used **only** to decide which single source a
    bare name belongs to; it authorises nothing, which is the column layer's job. Empty
    is the fail-closed default: with no corpus knowledge, a bare name in a multi-source
    scope has no unique binding and refuses.
    """
    views = tuple(views)
    tables: dict[str, str] = {}
    sources: dict[int, dict[str, BoundSource]] = {}
    for view in views:
        sources[id(view.scope)] = _classify_sources(view, default_schema, tables)

    columns: list[ColumnBinding] = []
    opaque: dict[str, str] = {}
    for view in views:
        refusal = _structural_refusal(view)
        if refusal is not None:
            return refusal
        local = sources[id(view.scope)]
        unresolvable = [source for source in local.values() if source.kind == "unresolvable"]
        if unresolvable:
            return LayerRefusal(
                "r_table_function",
                "a FROM source that is not a table produces nothing to license",
            )
        refusal = _bind_columns(view, local, sources, columns, opaque, known_columns)
        if refusal is not None:
            return refusal
        refusal = _bind_join_keys(view, local, columns)
        if refusal is not None:
            return refusal

    if not views:
        return LayerRefusal("r_unbound_reference", "the statement has no query scope")
    return Bindings(tables=tables, columns=tuple(columns), opaque=opaque)
