"""Corpus structure projection for ``resolve`` / ``connect`` (ADR 0005 §2.8.2).

Built once at index time. Returns ``(structure, problems)``. Join endpoints bind
by exact table-asset lookup (no first-match guess). Also hosts
:func:`complete_joins` (conjunctive rule); graph search stays in ``connect``.
"""


from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

from governed_bi.corpus.identity import table_id as table_id_of
from governed_bi.corpus.validate import Problem
from governed_bi.register.assets import AssetType

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
    """Projections ``resolve`` / ``connect`` run on, keyed on asset ids (not physical names)."""

    #: Undirected table-to-table edges, canonicalised by ``canon_edge``. Self-joins are
    #: **excluded**: a loop makes an isolated terminal look adjacent, hiding a genuinely
    #: disconnected table from ``connect``'s missing-terminal check. They stay in
    #: :attr:`joins_by_edge`, so their keys still reach the prompt.
    join_edges: frozenset[tuple[str, str]]
    #: ``asset id -> ids it points at``. **Disjunctive**, which is what ``resolve``
    #: needs and why the conjunctive last row of §2.8 is not in here.
    references: Mapping[str, frozenset[str]]
    #: ``asset id -> AssetType value``.
    asset_types: Mapping[str, str]
    #: ``table asset id -> schema``. Read for ``crossings`` accounting.
    table_schemas: Mapping[str, str]
    #: ``asset id -> the schema it votes for in route``, from the declared
    #: :class:`~governed_bi.register.assets.TagRule` table via
    #: :func:`~governed_bi.retrieve.index.schema_tag_for` -- not a second local rule.
    schema_tags: Mapping[str, str]
    #: ``canonical edge -> the join asset ids on it``, sorted. Several relationships per
    #: table pair is the normal case ADR 0005 §1.2 put the ON digest in the join id for;
    #: collapsing them cost 33 of 57 schemas an edge in v1 (2026-07-29, pre-2026-08-05).
    joins_by_edge: Mapping[tuple[str, str], tuple[str, ...]]


def build_structure(
    assets: Iterable[Any],
) -> tuple[CorpusStructure, list[Problem]]:
    """Project ``assets`` into the structure retrieval runs on.

    A pure function of the asset set: no question, no config, no clock, order-independent
    and idempotent. Asserted by ``tests/retrieve/test_structure_contract.py``.
    """
    problems: list[Problem] = []
    by_id: dict[str, Any] = {}
    for asset in assets:
        aid = _attr(asset, "id")
        if aid is None:
            problems.append(Problem(where="<asset>", reason="asset has no id"))
            continue
        # First-wins on a repeated id, and no problem recorded: two ``JoinAsset``s
        # differing only in endpoint qualification mint the same ``join_id`` by
        # construction (ADR 0005 §1.2), so reporting the second would make a valid
        # corpus look broken. A genuinely duplicated id is refused by ``build_index``.
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


def complete_joins(licensed: Set[Any], structure: CorpusStructure) -> frozenset[str]:
    """Every join whose **both** endpoints are in ``licensed`` (§2.8's last row).

    Runs **after** ``connect``, not inside ``resolve`` (§2.8.1): a Steiner point exists to
    sit on a join path, so the pairs that most need their ``on`` clause in the prompt are
    created after ``resolve`` runs. Returned joins are ``pulled_in`` and exempt from the
    join budget — they never pass through ``apply_budgets``, which caps ranked hits only.
    """
    ids = {str(x) for x in licensed}
    out: set[str] = set()
    for (left, right), joins in structure.joins_by_edge.items():
        if left in ids and right in ids:
            out.update(joins)
    return frozenset(out)


# ── endpoint reconciliation ───────────────────────────────────────────────────


def table_lookup(tables: Mapping[str, Any]) -> Mapping[str, frozenset[str]]:
    """Every spelling a table endpoint may use -> the table ids that answer to it.

    Four keys per table: the asset id, ``table_id(schema, physical_name)``, the bare
    ``physical_name``, and the **engine spelling** ``{schema}.{physical_name}``. The last
    is the physical→id map ADR 0008 D1 implies — an asset id carries the *slug*
    (``airline.Air_Carriers_66c534``) while SQL carries ``FROM airline."Air Carriers"``, so
    without it every few-shot citing a slugged table reports "matches no table asset".

    The value is a **set**: a bare name present in two schemas answers with two ids, and
    :func:`bind_endpoint` refuses rather than guessing.

    Public because ``serve/session.py`` has to answer "does this endpoint name an excluded
    table?" before ``build_structure`` runs, and a second copy of the four-key policy here is
    how ``airline."Air Carriers"`` ended up with no table asset in the first place.
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
    """``(table id, None)`` or ``(None, why it was refused)``. Never a guess.

    ``scope`` is the *referring asset's own* schema. A ``ColumnAsset`` declares ``schema``
    beside its bare ``parent_table``, so scoping is reading a qualification the corpus
    made, not inventing one. ``JoinAsset`` has no ``schema`` field by design (ADR 0005
    §1.2), so its bare endpoints get no scope and are genuinely ambiguous in a pooled lake.
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


# ── one linker per §2.8 closure row ───────────────────────────────────────────


def _link_column(
    aid: str,
    asset: Any,
    lookup: Mapping[str, frozenset[str]],
    references: dict[str, set[str]],
    problems: list[Problem],
) -> None:
    """``ColumnAsset`` hit pulls in its ``TableAsset``."""
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
    """A ``TableAsset`` in the set pulls in all of its columns."""
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
    """``TermAsset`` hit pulls in its ``binding`` target."""
    binding = _attr(asset, "binding")
    if binding is None:
        return  # an unbound term is a state, not a defect (validate.py, TAG_RULE_FIELDS)
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
    """``MetricAsset`` hit pulls in its ``base_table`` **and its ``dimensions``** (D4).

    A dimension is a **column** id and may name a column of a table other than
    ``base_table`` (grouped by country = a column of the joined country table), so it is
    looked up in ``by_id`` rather than bound through ``lookup``, which answers for tables
    only. Resolving them is also what makes a dangling dimension reportable at all.
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
                    # Degradation, not a stop (ADR 0008 D9): the metric still renders and
                    # resolves its base table, so this costs recall, not correctness.
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
    """``FewShotAsset`` hit pulls in the tables its SQL references.

    §2.8: a parse failure resolves nothing for that few-shot and is **recorded**,
    never silently dropped.
    """
    sql = _attr(asset, "sql")
    if not sql:
        return
    names, parse_error = _sql_table_names(str(sql))
    if parse_error is not None:
        # Advisory content, so non-fatal: an unusable few-shot costs recall and cannot
        # mis-license anything, because nothing downstream keys on it (ADR 0008 D9).
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
    """``JoinAsset`` hit pulls in both endpoint tables, and contributes one edge.

    Endpoint closure belongs here because it expands the terminal set ``connect`` must
    then join. The reverse direction is :func:`complete_joins` (§2.8.1: it cannot live
    in a disjunctive fixpoint).
    """
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


# ── schema tags ───────────────────────────────────────────────────────────────


def _schema_tags(
    by_id: Mapping[str, Any],
    types: Mapping[str, str | None],
    table_schemas: Mapping[str, str],
    lookup: Mapping[str, frozenset[str]],
) -> Mapping[str, str]:
    """Every asset's route vote, through the declared ``TagRule`` table.

    This reads the endpoints and delegates the rule to
    :func:`~governed_bi.retrieve.index.schema_tag_for`; a second per-type ``if`` ladder
    here would be a second answer to "which schema does a cross-schema join vote for".
    """
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
    """Give a still-untagged bound asset the tag of what it is bound to.

    Closes the pooled lake's licensing leak: ``TagRule.binding_target`` reads the target's
    own ``schema``, a metric has none, so a term bound to a metric was untagged — and an
    untagged asset is carried into pass two *unconditionally*, letting one lexical hit
    license another schema's tables that ``connect`` can never join. 136 untagged terms
    on the gold-semantic-layer corpus; it declined even at ``route_top_n = 1``.

    A fixpoint, not capped recursion: ``term → term → metric`` is legitimate and a cycle
    must stop rather than raise. Bounded by ``len(bound_to)``, so it terminates on any
    graph. Adds no rule — the propagated tag is whatever ``TagRule`` gave the target.
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
    """A binding target's *own* schema field, one level only.

    ``SchemaAsset`` answers with its ``name``, everything else with ``schema``. ``join``,
    ``metric`` and ``term`` have none and get ``None``; :func:`_tag_through_bindings`
    carries their *derived* tag across. One level deep on purpose — merging "read a
    field" with "follow a chain" is how the chain became invisible.
    """
    if asset is None:
        return None
    if _type_of(asset) == AssetType.schema.value:
        return _str_or_none(_attr(asset, "name"))
    return _str_or_none(_attr(asset, "schema"))


# ── SQL and attribute access ──────────────────────────────────────────────────


def _sql_table_names(sql: str) -> tuple[tuple[str, ...], str | None]:
    """``(table names the statement reads, parse error)``. CTE names are excluded.

    A CTE is a name the statement defines, not one it references; reporting it would turn
    every well-formed few-shot with a ``WITH`` clause into a curation problem (decision #5).
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
    """One field of an asset, whether it is a dataclass or a raw mapping.

    Both reach here: ``corpus.store`` yields dataclasses, ``configurable["corpus"]`` may
    hold mappings that never went through ``parse``.
    """
    if isinstance(asset, Mapping):
        return asset.get(name)
    return getattr(asset, name, None)


def _type_of(asset: Any) -> str | None:
    """The ``AssetType`` value of an asset, or ``None`` when it declares none."""
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
