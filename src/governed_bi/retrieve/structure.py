"""Projections for closure and join choice, built once. Endpoints bind by exact lookup. See ADR 0005."""


from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

from governed_bi.corpus.identity import table_id as table_id_of
from governed_bi.corpus.validate import Problem
from governed_bi.registry.assets import AssetType

from .connect import canon_edge
from .index import schema_tag_for

__all__ = [
    "CorpusStructure",
    "bind_endpoint",
    "build_structure",
    "complete_joins",
    "table_lookup",
]


@dataclass(frozen=True, slots=True)
class CorpusStructure:
    #: Undirected. Self-joins are excluded: a loop hides an isolated terminal.
    join_edges: frozenset[tuple[str, str]]
    #: Disjunctive, which is what closure needs. The conjunctive join rule is not here.
    references: Mapping[str, frozenset[str]]
    asset_types: Mapping[str, str]
    table_schemas: Mapping[str, str]
    #: The schema the asset votes for, from the register tag rule, not a second copy.
    schema_tags: Mapping[str, str]
    #: Every join id on a table pair, sorted. Collapsing a pair to one edge drops joins.
    joins_by_edge: Mapping[tuple[str, str], tuple[str, ...]]


def build_structure(
    assets: Iterable[Any],
) -> tuple[CorpusStructure, list[Problem]]:
    """A pure function of the asset set: no question, no config, no clock."""
    problems: list[Problem] = []
    by_id: dict[str, Any] = {}
    for asset in assets:
        aid = _attr(asset, "id")
        if aid is None:
            problems.append(Problem(where="<asset>", reason="asset has no id"))
            continue
        # First id wins. Two joins that differ only in qualification mint one id.
        # A duplicated id is refused when the index is built.
        by_id.setdefault(str(aid), asset)

    types = {aid: _type_of(asset) for aid, asset in by_id.items()}
    tables = {aid: a for aid, a in by_id.items() if types.get(aid) == AssetType.table.value}
    table_schemas = {aid: str(_attr(a, "schema") or "") for aid, a in tables.items()}
    lookup = table_lookup(tables)

    references: dict[str, set[str]] = {}
    joins_by_edge: dict[tuple[str, str], list[str]] = {}
    edges: set[tuple[str, str]] = set()

    for aid, asset in sorted(by_id.items()):
        kind = types.get(aid)
        if kind == AssetType.column.value:
            _link_column(aid, asset, lookup, references, problems)
        elif kind == AssetType.table.value:
            _link_table(aid, asset, by_id, references, problems)
        elif kind == AssetType.term.value:
            _link_term(aid, asset, by_id, references, problems)
        elif kind == AssetType.metric.value:
            _link_metric(aid, asset, by_id, lookup, references, problems)
        elif kind == AssetType.few_shot.value:
            _link_few_shot(aid, asset, lookup, references, problems)
        elif kind == AssetType.join.value:
            _link_join(aid, asset, lookup, references, joins_by_edge, edges, problems)

    structure = CorpusStructure(
        join_edges=frozenset(edges),
        references={aid: frozenset(ids) for aid, ids in references.items() if ids},
        asset_types={aid: kind for aid, kind in types.items() if kind is not None},
        table_schemas={aid: s for aid, s in table_schemas.items() if s},
        schema_tags=_schema_tags(by_id, types, table_schemas, lookup),
        joins_by_edge={edge: tuple(sorted(set(ids))) for edge, ids in joins_by_edge.items()},
    )
    return structure, problems


def complete_joins(retrieved_tables: Set[Any], structure: CorpusStructure) -> frozenset[str]:
    """Joins whose both endpoints were retrieved. Runs after ``connect``, not inside closure.

    A Steiner point exists to sit on a path, so those pairs appear only after closure. The joins come back as pulled-in
    and do not consume the ranked-hit cap.
    """
    ids = {str(x) for x in retrieved_tables}
    out: set[str] = set()
    for (left, right), joins in structure.joins_by_edge.items():
        if left in ids and right in ids:
            out.update(joins)
    return frozenset(out)


def table_lookup(tables: Mapping[str, Any]) -> Mapping[str, frozenset[str]]:
    """Every spelling of a table endpoint, mapped to the ids that answer to it.

    The value is a set: a bare name in two schemas must not be guessed. Public because the session asks "is this an
    excluded table?" before this projection is built, and a second copy of the spellings drifted.
    """
    out: dict[str, set[str]] = {}
    for aid, asset in tables.items():
        physical = _attr(asset, "physical_name")
        schema = _attr(asset, "schema")
        keys = {aid}
        if physical:
            keys.add(str(physical))
            if schema:
                keys.add(table_id_of(str(schema), str(physical)))
                keys.add(f"{schema}.{physical}")
        for key in keys:
            out.setdefault(key, set()).add(aid)
    return {key: frozenset(ids) for key, ids in out.items()}


def bind_endpoint(
    endpoint: Any,
    lookup: Mapping[str, frozenset[str]],
    *,
    scope: Any = None,
) -> tuple[str | None, str | None]:
    """``(table id, None)`` or ``(None, why)``. Never a guess.

    ``scope`` is the referring asset's own schema. A join has no schema field, so a bare endpoint stays unscoped in a
    pooled lake.
    """
    if endpoint is None or not str(endpoint).strip():
        return None, "endpoint is empty"
    name = str(endpoint)
    if scope:
        scoped = lookup.get(table_id_of(str(scope), name.rsplit(".", 1)[-1]))
        if scoped and len(scoped) == 1:
            return next(iter(scoped)), None

    candidates = lookup.get(name) or frozenset()
    if len(candidates) == 1:
        return next(iter(candidates)), None
    if not candidates:
        return None, (
            f"{name!r} matches no table asset. An endpoint that names nothing is a "
            "curation defect, and it must surface where the corpus is built rather "
            "than as a missing_join_path decline three layers away"
        )
    return None, (
        f"{name!r} is ambiguous: {', '.join(sorted(candidates))}. One physical name in "
        "two schemas is the normal shape of a pooled lake, and left-most or first-match "
        "resolution there fails open -- it licenses a table in the wrong schema and "
        "charges crossings to the wrong pair. So the edge is dropped and this is recorded"
    )


def _link_column(
    aid: str,
    asset: Any,
    lookup: Mapping[str, frozenset[str]],
    references: dict[str, set[str]],
    problems: list[Problem],
) -> None:
    parent = _attr(asset, "parent_table")
    bound, why = bind_endpoint(parent, lookup, scope=_attr(asset, "schema"))
    if bound is None:
        problems.append(Problem(where=aid, reason=f"parent_table {why}"))
        return
    references.setdefault(aid, set()).add(bound)


def _link_table(
    aid: str,
    asset: Any,
    by_id: Mapping[str, Any],
    references: dict[str, set[str]],
    problems: list[Problem],
) -> None:
    declared = _attr(asset, "columns") or ()
    for column_id in declared:
        cid = str(column_id)
        if cid in by_id:
            references.setdefault(aid, set()).add(cid)
        else:
            problems.append(
                Problem(
                    where=aid,
                    reason=(
                        f"declares column {cid!r}, which is not in the asset set. "
                        "Licensing an id with no asset renders nothing and reads as a "
                        "column the analyst may not see"
                    ),
                )
            )


def _link_term(
    aid: str,
    asset: Any,
    by_id: Mapping[str, Any],
    references: dict[str, set[str]],
    problems: list[Problem],
) -> None:
    binding = _attr(asset, "binding")
    if binding is None:
        return  # an unbound term is a state, not a defect
    target = _attr(binding, "target_id")
    if target is None or str(target) not in by_id:
        problems.append(
            Problem(where=aid, reason=f"binding target {target!r} is not in the asset set")
        )
        return
    references.setdefault(aid, set()).add(str(target))


def _link_metric(
    aid: str,
    asset: Any,
    by_id: Mapping[str, Any],
    lookup: Mapping[str, frozenset[str]],
    references: dict[str, set[str]],
    problems: list[Problem],
) -> None:
    """Pulls in ``base_table`` and ``dimensions``.

    A dimension is a column id and may belong to another table, so it is looked up by id, not through the table spelling
    map.
    """
    bound, why = bind_endpoint(_attr(asset, "base_table"), lookup)
    if bound is None:
        problems.append(Problem(where=aid, reason=f"base_table {why}"))
        return
    references.setdefault(aid, set()).add(bound)

    for dimension in _attr(asset, "dimensions") or ():
        did = str(dimension)
        if did in by_id:
            references[aid].add(did)
        else:
            problems.append(
                Problem(
                    where=aid,
                    reason=(
                        f"dimension {did!r} is not an asset id. A bare column name is "
                        "not one either: it is unambiguous inside a table and this "
                        "field is not scoped to one, so the reference cannot be "
                        "resolved and the column never reaches the prompt"
                    ),
                    # Recall cost, not a stop (ADR 0008). The metric still resolves its base table.
                    fatal=False,
                )
            )


def _link_few_shot(
    aid: str,
    asset: Any,
    lookup: Mapping[str, frozenset[str]],
    references: dict[str, set[str]],
    problems: list[Problem],
) -> None:
    sql = _attr(asset, "sql")
    if not sql:
        return
    names, parse_error = _sql_table_names(str(sql))
    if parse_error is not None:
        # Non-fatal: an unusable example costs recall and cannot mis-bind anything (ADR 0008).
        problems.append(
            Problem(where=aid, reason=f"sql did not parse: {parse_error}", fatal=False)
        )
        return
    scope = _attr(asset, "schema")
    for name in names:
        bound, why = bind_endpoint(name, lookup, scope=scope)
        if bound is None:
            problems.append(
                Problem(where=aid, reason=f"sql references a table that {why}", fatal=False)
            )
            continue
        references.setdefault(aid, set()).add(bound)


def _link_join(
    aid: str,
    asset: Any,
    lookup: Mapping[str, frozenset[str]],
    references: dict[str, set[str]],
    joins_by_edge: dict[tuple[str, str], list[str]],
    edges: set[tuple[str, str]],
    problems: list[Problem],
) -> None:
    left, left_why = bind_endpoint(_attr(asset, "left_table"), lookup)
    right, right_why = bind_endpoint(_attr(asset, "right_table"), lookup)
    for side, why in (("left_table", left_why), ("right_table", right_why)):
        if why is not None:
            problems.append(
                Problem(
                    where=aid,
                    reason=(
                        f"{side} {why}. The whole edge is dropped, not the one endpoint: "
                        "half an edge would license a table with no way to reach it"
                    ),
                )
            )
    if left is None or right is None:
        return

    references.setdefault(aid, set()).update({left, right})
    edge = canon_edge(left, right)
    joins_by_edge.setdefault((str(edge[0]), str(edge[1])), []).append(aid)
    if left != right:
        edges.add((str(edge[0]), str(edge[1])))


def _schema_tags(
    by_id: Mapping[str, Any],
    types: Mapping[str, str | None],
    table_schemas: Mapping[str, str],
    lookup: Mapping[str, frozenset[str]],
) -> Mapping[str, str]:
    """Delegates the vote to ``schema_tag_for``. A second per-type ladder would be a second answer."""
    out: dict[str, str] = {}
    bound_to: dict[str, str] = {}
    for aid, asset in by_id.items():
        kind = types.get(aid)
        if kind is None:
            continue
        try:
            asset_type = AssetType(kind)
        except ValueError:  # pragma: no cover - _type_of only yields register values
            continue
        parent, _ = bind_endpoint(_attr(asset, "parent_table"), lookup, scope=_attr(asset, "schema"))
        base, _ = bind_endpoint(_attr(asset, "base_table"), lookup)
        left, _ = bind_endpoint(_attr(asset, "left_table"), lookup)
        binding = _attr(asset, "binding")
        target_id = str(_attr(binding, "target_id")) if binding is not None else ""
        target = by_id.get(target_id)
        if target is not None:
            bound_to[aid] = target_id
        tag = schema_tag_for(
            asset_type,
            name=_str_or_none(_attr(asset, "name")),
            schema=_str_or_none(_attr(asset, "schema")),
            parent_schema=table_schemas.get(parent or ""),
            base_table_schema=table_schemas.get(base or ""),
            binding_schema=_own_schema(target),
            left_table_schema=table_schemas.get(left or ""),
        )
        if tag:
            out[aid] = str(tag)
    _tag_through_bindings(out, bound_to)
    return out


def _tag_through_bindings(out: dict[str, str], bound_to: Mapping[str, str]) -> None:
    """Copy a bound target's tag onto an asset that still has none.

    An untagged asset is carried into the next pass unconditionally, so one hit can bring in tables from another schema.
    A fixpoint, because a binding chain may cycle.
    """
    for _ in range(len(bound_to)):
        progressed = False
        for aid, target_id in bound_to.items():
            if aid in out:
                continue
            tag = out.get(target_id)
            if tag:
                out[aid] = tag
                progressed = True
        if not progressed:
            return


def _own_schema(asset: Any) -> str | None:
    """The target's own schema field, one level only.

    A schema asset answers with its name. Joins, metrics, and terms have none; the binding walk carries their derived
    tag.
    """
    if asset is None:
        return None
    if _type_of(asset) == AssetType.schema.value:
        return _str_or_none(_attr(asset, "name"))
    return _str_or_none(_attr(asset, "schema"))


def _sql_table_names(sql: str) -> tuple[tuple[str, ...], str | None]:
    """Table names the statement reads. CTE names are excluded.

    A CTE is defined by the statement, not referenced. Reporting it would flag every well-formed example that uses
    ``WITH``.
    """
    try:
        tree = sqlglot.parse_one(sql, dialect="postgres")
    except SqlglotError as err:
        return (), str(err)
    if tree is None:
        return (), "statement is empty"
    defined = {
        str(cte.alias_or_name).casefold() for cte in tree.find_all(exp.CTE) if cte.alias_or_name
    }
    names: list[str] = []
    for table in tree.find_all(exp.Table):
        name = str(table.name or "")
        if not name or name.casefold() in defined:
            continue
        qualified = f"{table.db}.{name}" if table.db else name
        if qualified not in names:
            names.append(qualified)
    return tuple(names), None


def _attr(asset: Any, name: str) -> Any:
    if isinstance(asset, Mapping):
        return asset.get(name)
    return getattr(asset, name, None)


def _type_of(asset: Any) -> str | None:
    raw = _attr(asset, "asset_type")
    if raw is None:
        return None
    if isinstance(raw, AssetType):
        return raw.value
    try:
        return AssetType(str(raw)).value
    except ValueError:
        return None


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)
