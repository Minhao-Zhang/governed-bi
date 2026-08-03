"""The corpus structure projection — ADR 0005 §2.8.2.

``resolve`` and ``connect`` are both total functions of data neither of them has.
Until 2026-08-03 ``serve/state.py`` declared five inputs for them -- ``join_edges``,
``references``, ``asset_types``, ``table_schemas``, ``schema_tags`` -- under the
comment *"F1 test / wiring hooks (optional)"*, and **all five were read and none was
ever written**, by ``src/``, by ``tests/`` or by the eval harness. So ``connect`` ran
on an empty edge set on every turn that has ever executed and declined
``missing_join_path`` the moment a turn licensed two tables; single-table turns
answered, which is why a green suite and a live eval both missed it.

Those five are not five hooks. They are **one projection of the asset set**, they are
pure functions of it, and they hold no per-turn information -- so one module builds
all five, once, beside the index. §2.2 already settled the identical question for
schema tags (*"computed at build, not query time"*) and the argument is not cost: a
per-turn derivation is a place where two turns can disagree about the shape of the
corpus, and a run whose turns disagree is not comparable to anything.

**It returns ``(structure, problems)``**, per decision #5. A corpus that lost half its
edges must not be indistinguishable from a corpus that is small.

**Endpoint reconciliation may not guess.** ``connect``'s nodes are the identifiers in
``licensed`` -- asset ids. ``JoinAsset`` carries ``left_table`` / ``right_table`` as
physical names, **bare or qualified**, and ``corpus/validate.py``'s ``_bare()``
explicitly declines to settle which. Binding an edge is therefore a lookup with three
outcomes: exactly one table asset binds it; more than one drops it *and records a
problem*; none does the same. One physical name in two schemas is the normal shape of
a pooled lake, and first-match there is not a lost edge but a **licensing leak** -- a
Steiner point in the wrong schema, licensed, with ``crossings`` charged to the wrong
pair. Dropping alone fails closed but silently, and the silence resurfaces as
``missing_join_path`` on a turn that looks ordinary.

**What this module deliberately is not.** There is exactly one graph search in ``src/``
(:func:`~governed_bi.retrieve.connect.connect`) and exactly one closure
(:func:`~governed_bi.retrieve.resolve.resolve`). This module builds their inputs and
adds one thing neither can express: :func:`complete_joins`, the **conjunctive** rule
from §2.8's last row. ``resolve`` is a fixpoint over ``Mapping[id, set[id]]`` where
every edge is disjunctive, so encoding "both endpoints pull in the join" as
``table -> joins touching it`` would let one endpoint pull the join and the join pull
its other endpoint: FK-neighbourhood expansion by one hop from every hit table, which
is exactly what §2.9 turned off (``expand_hops = 0``, v1's 1 recorded as wrong).
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

__all__ = ["CorpusStructure", "build_structure", "complete_joins"]


@dataclass(frozen=True, slots=True)
class CorpusStructure:
    """The five projections ``resolve`` and ``connect`` run on, plus the join index.

    Every field is keyed on **asset ids**, never on physical names. That is the one
    property nothing else in the system asserts: an edge carrying
    ``("customers", "orders")`` is not wrong-looking anywhere -- ``_adjacency`` builds
    happily, :func:`~governed_bi.retrieve.connect.canon_edge` canonicalises happily,
    and ``connect`` then reports every terminal missing and declines. The two
    namespaces never meet, so nothing raises.
    """

    #: Undirected table-to-table edges, canonicalised by ``canon_edge``. Self-joins
    #: are **excluded**: a loop makes an isolated terminal look adjacent, which would
    #: hide a genuinely disconnected table from ``connect``'s missing-terminal check.
    #: They remain in :attr:`joins_by_edge`, so their keys still reach the prompt.
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
    #: ``canonical edge -> the join asset ids on it``, sorted. Several relationships
    #: between one table pair is the normal case ADR 0005 §1.2 put the ON-clause
    #: digest in the join id for; collapsing them cost 33 of 57 schemas an edge in v1.
    joins_by_edge: Mapping[tuple[str, str], tuple[str, ...]]


def build_structure(
    assets: Iterable[Any],
) -> tuple[CorpusStructure, list[Problem]]:
    """Project ``assets`` into the structure retrieval runs on.

    A pure function of the asset set: no question, no config, no clock. The same
    assets in any order produce an equal structure, and building twice produces equal
    structures -- asserted by ``tests/retrieve/test_structure_contract.py``, because a
    signature that ever needs one of those three is the design changing.
    """
    problems: list[Problem] = []
    by_id: dict[str, Any] = {}
    for asset in assets:
        aid = _attr(asset, "id")
        if aid is None:
            problems.append(Problem(where="<asset>", reason="asset has no id"))
            continue
        # First-wins on a repeated id, and **no problem is recorded for it**. Two
        # ``JoinAsset``s that differ only in the qualification of an endpoint are the
        # same relationship and mint the same ``join_id`` by construction (ADR 0005
        # §1.2 puts the ON-clause digest in the id precisely so they do), so reporting
        # the second as a defect would make a valid corpus look broken. A repeated id
        # that is genuinely two different assets is refused loudly one layer up, by
        # :func:`~governed_bi.retrieve.index.build_index`.
        by_id.setdefault(str(aid), asset)

    types = {aid: _type_of(asset) for aid, asset in by_id.items()}
    tables = {aid: a for aid, a in by_id.items() if types.get(aid) == AssetType.table.value}
    table_schemas = {aid: str(_attr(a, "schema") or "") for aid, a in tables.items()}
    lookup = _table_lookup(tables)

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
            _link_metric(aid, asset, lookup, references, problems)
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

    Runs **after** ``connect``, not inside ``resolve`` (§2.8.1). Two reasons, and the
    second is not a preference: a Steiner point's whole purpose is to sit on a join
    path, so the table pairs that most need their ``on`` clause in the prompt are
    exactly the ones created *after* ``resolve`` has run. Completing joins earlier
    reproduces draft 2's failure one stage later -- a multi-hop question reaching the
    model with none of the keys for the hops ``connect`` chose.

    Total, idempotent, and a function of a set. Joins it returns are ``pulled_in`` and
    exempt from the join budget: they never pass through
    :func:`~governed_bi.retrieve.budget.apply_budgets`, which caps ranked hits only.
    """
    ids = {str(x) for x in licensed}
    out: set[str] = set()
    for (left, right), joins in structure.joins_by_edge.items():
        if left in ids and right in ids:
            out.update(joins)
    return frozenset(out)


# ── endpoint reconciliation ───────────────────────────────────────────────────


def _table_lookup(tables: Mapping[str, Any]) -> Mapping[str, frozenset[str]]:
    """Every spelling a table endpoint may use -> the table ids that answer to it.

    Three keys per table, all of them spellings a valid corpus is allowed to carry:
    the asset id, ``table_id(schema, physical_name)`` (the declared convention, from
    :mod:`governed_bi.corpus.identity` rather than a second f-string), and the bare
    ``physical_name``. The value is a **set**, which is the whole mechanism: a bare
    name present in two schemas answers with two ids, and that is not a lost edge to
    be patched up but the case where a guess fails open.
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
        for key in keys:
            out.setdefault(key, set()).add(aid)
    return {key: frozenset(ids) for key, ids in out.items()}


def _bind(
    endpoint: Any,
    lookup: Mapping[str, frozenset[str]],
    *,
    scope: Any = None,
) -> tuple[str | None, str | None]:
    """``(table id, None)`` or ``(None, why it was refused)``. Never a guess.

    ``scope`` is the *referring asset's own* schema, where it has one. Using it is not
    a guess: a ``ColumnAsset`` declares ``schema`` alongside its bare ``parent_table``,
    so ``sales_a`` + ``customers`` is a fully qualified statement the corpus made, and
    resolving it globally would manufacture an ambiguity the author did not have.
    ``JoinAsset`` has no ``schema`` field **by design** (ADR 0005 §1.2), so its bare
    endpoints get no scope and a pooled lake makes them genuinely ambiguous.
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
    bound, why = _bind(parent, lookup, scope=_attr(asset, "schema"))
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
    lookup: Mapping[str, frozenset[str]],
    references: dict[str, set[str]],
    problems: list[Problem],
) -> None:
    """``MetricAsset`` hit pulls in its ``base_table``."""
    bound, why = _bind(_attr(asset, "base_table"), lookup)
    if bound is None:
        problems.append(Problem(where=aid, reason=f"base_table {why}"))
        return
    references.setdefault(aid, set()).add(bound)


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
        problems.append(Problem(where=aid, reason=f"sql did not parse: {parse_error}"))
        return
    scope = _attr(asset, "schema")
    for name in names:
        bound, why = _bind(name, lookup, scope=scope)
        if bound is None:
            problems.append(Problem(where=aid, reason=f"sql references a table that {why}"))
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

    Endpoint closure stays here, in ``resolve``'s input, because it expands the
    terminal set ``connect`` then has to connect. The reverse direction -- both tables
    pull in the join -- is :func:`complete_joins`, and §2.8.1 records why it cannot
    live in a disjunctive fixpoint.
    """
    left, left_why = _bind(_attr(asset, "left_table"), lookup)
    right, right_why = _bind(_attr(asset, "right_table"), lookup)
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

    :func:`~governed_bi.retrieve.index.schema_tag_for` *is* that table in function
    form, so this reads the endpoints it needs and delegates the rule. A second
    per-type ``if`` ladder here would be a second answer to "which schema does a
    cross-schema join vote for".
    """
    out: dict[str, str] = {}
    for aid, asset in by_id.items():
        kind = types.get(aid)
        if kind is None:
            continue
        try:
            asset_type = AssetType(kind)
        except ValueError:  # pragma: no cover - _type_of only yields register values
            continue
        parent, _ = _bind(_attr(asset, "parent_table"), lookup, scope=_attr(asset, "schema"))
        base, _ = _bind(_attr(asset, "base_table"), lookup)
        left, _ = _bind(_attr(asset, "left_table"), lookup)
        binding = _attr(asset, "binding")
        target = by_id.get(str(_attr(binding, "target_id"))) if binding is not None else None
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
    return out


def _own_schema(asset: Any) -> str | None:
    """A binding target's schema, one level only.

    ``SchemaAsset`` answers with its ``name``; everything else with its ``schema``
    field where it has one. Deliberately not recursive: a term bound to a term is not
    a shape ADR 0005 gives a tag rule for, and an untagged term is a **state** --
    it does not vote in ``route`` but is carried into pass two unconditionally.
    """
    if asset is None:
        return None
    if _type_of(asset) == AssetType.schema.value:
        return _str_or_none(_attr(asset, "name"))
    return _str_or_none(_attr(asset, "schema"))


# ── SQL and attribute access ──────────────────────────────────────────────────


def _sql_table_names(sql: str) -> tuple[tuple[str, ...], str | None]:
    """``(table names the statement reads, parse error)``. CTE names are excluded.

    A CTE is a name the statement defines rather than one it references, so reporting
    it as an unresolvable table would turn every well-formed few-shot with a ``WITH``
    clause into a curation problem -- and a problem list nobody can act on is a silent
    skip with extra steps (decision #5).
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

    Both shapes reach here: ``corpus.store`` yields dataclasses, and
    ``configurable["corpus"]`` may hold mappings that never went through ``parse``.
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
