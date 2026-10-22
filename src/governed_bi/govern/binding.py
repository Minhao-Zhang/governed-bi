"""Each reference binds to one source in its scope, or a named ancestor. An allowlist is not a binding. See ADR 0006."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from sqlglot import expressions as exp
from sqlglot.errors import OptimizeError
from sqlglot.optimizer.scope import Scope

from .identifiers import column_key, fold, table_key
from .scopes import ScopeView

__all__ = ["BoundSource", "ColumnBinding", "LayerRefusal", "Bindings", "bind"]


@dataclass(frozen=True, slots=True)
class BoundSource:
    """One source a reference can bind to."""

    #: Alias, or the table name when there is no alias.
    reference: str
    #: ``base``, ``derived``, or ``unresolvable`` (a table-valued function: no Identifier).
    #: ``bind`` collects the third and refuses ``r_table_function``.
    kind: str
    schema: str | None = None
    name: str | None = None
    #: :func:`~governed_bi.govern.identifiers.table_key` output. Base sources only. The slug
    #: stays: the set this is compared with is keyed on asset ids. See ADR 0008.
    key: str | None = None


@dataclass(frozen=True, slots=True)
class ColumnBinding:
    """A column reference and the one base source it bound to."""

    reference: str
    table_key: str
    #: ``{schema}.{slug(table)}.{slug(column)}``, folded. The column layer's only input.
    column_key: str


@dataclass(frozen=True, slots=True)
class LayerRefusal:
    """A rule id and its detail. The layer comes from ``layers.RULES``, not from here."""

    rule_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class Bindings:
    """Every resolved reference in the statement."""

    #: reference → table key, for the table layer.
    tables: dict[str, str] = field(default_factory=dict)
    columns: tuple[ColumnBinding, ...] = ()
    #: Derived source or projection alias. Recorded: an unrecorded reference is
    #: indistinguishable from one the walk never reached.
    opaque: dict[str, str] = field(default_factory=dict)

    def as_bound(self) -> dict[str, str]:
        """Every reference to the source it bound to, including opaque ones."""
        out = dict(self.tables)
        out.update({binding.reference: binding.table_key for binding in self.columns})
        out.update(self.opaque)
        return out


def _in_order_by(node: exp.Expr, root: exp.Expr) -> bool:
    """``ORDER BY`` resolves output names first. ``GROUP BY`` and ``HAVING`` stay column checks."""
    current: exp.Expr | None = node
    while current is not None and current is not root:
        if isinstance(current, exp.Order):
            return True
        current = current.parent
    return False


def _lookup(scope: Scope, name: str, sources: Mapping[int, Mapping[str, BoundSource]]) -> BoundSource | None:
    """This scope, then ancestors. A correlated reference names a source it does not select."""
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
    """Alias only. ``selected_sources``, not ``scope.sources``, which merges every CTE into every scope."""
    local: dict[str, BoundSource] = {}
    # Values are ``(node, source)``. The node is the FROM item; only the source is used.
    for alias, (_node, source) in view.scope.selected_sources.items():
        if isinstance(source, exp.Table):
            # A table-valued function is exp.Table wrapping a Func: no Identifier to bind.
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
    """Shapes with no binding, whatever the corpus says."""
    for node in view.nodes:
        if isinstance(node, exp.Join) and str(node.args.get("method") or "").upper() == "NATURAL":
            return LayerRefusal(
                "r_natural_join",
                "NATURAL JOIN joins on every common column, which is unenumerable, so "
                "no binding exists for its keys",
            )
        if isinstance(node, (exp.From, exp.Join)):
            source = node.this
            # ``Values`` is inline data. ``traverse_scope`` already models it as derived.
            # Refusing it blocks ordinary statements and reads no extra column.
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
            # One in-scope base declares this column, so there is one binding. Postgres
            # calls a name ambiguous only when two joined tables declare it. Without this,
            # the corpus-resolved name refuses.
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
    """A bare source name is the whole row when no in-scope column has that name. Empty ``known_columns`` falls through,
    and the column layer still refuses."""
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
    """``USING`` keys are not ``Column`` nodes, so a column sweep never sees an excluded join key."""
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
    """Two passes, because children are yielded first. ``known_columns`` authorises nothing. See ADR 0006."""
    views = tuple(views)
    tables: dict[str, str] = {}
    sources: dict[int, dict[str, BoundSource]] = {}
    for view in views:
        try:
            sources[id(view.scope)] = _classify_sources(view, default_schema, tables)
        except OptimizeError:
            # Two sources in one scope answer to one name. ``scope.sources`` keeps one alias
            # and files the other under its table name, which the alias is supposed to hide.
            # Postgres rejects the statement. Refusing is the answer that does not invent a binding.
            return LayerRefusal(
                "r_ambiguous_reference",
                "two sources in this scope answer to the same name, so no reference "
                "through that name has exactly one binding",
            )

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
