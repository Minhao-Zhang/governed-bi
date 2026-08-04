"""Filtering, sorting and bounded relationship walks over the loaded corpus. ADR 0009.

**The filterable columns are derived, never listed.** They come from each asset
dataclass's own fields plus :data:`~governed_bi.register.assets.ASSET_REGISTER`, so a field
added to ``corpus/schema.py`` becomes filterable with no change here and no change to the
UI — which renders its filter row from :func:`columns_for`. A hand-written column list in a
route would be the drift ``register/`` exists to end, and it would drift *silently*, because
a missing column is indistinguishable from a column somebody chose not to expose.

**A filter that cannot be applied is reported, not dropped.** :func:`apply_where` returns the
predicates it could not understand, and the route returns them to the client. Ignoring an
unknown field would show a filtered-looking list that is not filtered — the same class of
defect as a gate that never fires.

Filtering happens over the **in-memory corpus**, which is what retrieval runs on (ADR 0009
D5). Pushing it into SQL would make the browser query the lake instead of the semantic layer,
so an asset that failed to load would still appear — the corpus would look complete because
the database is.
"""

from __future__ import annotations

import dataclasses
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from enum import Enum
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from ..register.assets import ASSET_REGISTER, AssetType

__all__ = [
    "FieldKind",
    "OPS_BY_KIND",
    "DEFAULT_NODE_BUDGET",
    "columns_for",
    "parse_where",
    "apply_where",
    "sort_rows",
    "row_for",
    "predicate_columns",
    "subgraph",
]


class FieldKind(str, Enum):
    """What a column holds, which decides which operators it offers."""

    string = "string"
    number = "number"
    boolean = "boolean"
    enum = "enum"
    #: A reference to another asset id, or a list of them. Compared as text.
    ref = "ref"
    list = "list"
    #: A nested block (``governance``, ``audit``, ``reliability``). Only presence.
    block = "block"


#: Operators per kind. ``present`` is on everything because "is this set at all" is the one
#: question every optional field answers, and most of these fields are optional.
OPS_BY_KIND: Mapping[FieldKind, tuple[str, ...]] = {
    FieldKind.string: ("contains", "eq", "neq", "present"),
    FieldKind.number: ("eq", "gte", "lte", "present"),
    FieldKind.boolean: ("eq", "present"),
    FieldKind.enum: ("eq", "one_of", "present"),
    FieldKind.ref: ("contains", "eq", "present"),
    FieldKind.list: ("contains", "len_gte", "len_lte", "present"),
    FieldKind.block: ("present",),
}

#: Nodes returned when a caller names no budget. Chosen so a dagre layout in the browser
#: stays readable; the full lake is 656 tables, which is not a diagram a person can read.
DEFAULT_NODE_BUDGET = 120

#: Fields whose *value* is a reference to another asset. Declared because the annotation
#: cannot tell a reference from any other string, and treating one as free text would offer
#: ``contains`` on an id where ``eq`` is what a caller wants.
_REF_FIELDS = frozenset(
    {
        "parent_table",
        "base_table",
        "left_table",
        "right_table",
        "columns",
        "bound_terms",
        "dimensions",
        "related_terms",
        "references",
        "binding",
    }
)

_BLOCK_FIELDS = frozenset({"governance", "audit", "reliability", "provenance"})


def _kind_of(field: dataclasses.Field) -> FieldKind:
    if field.name in _BLOCK_FIELDS:
        return FieldKind.block
    annotation = str(field.type)
    # **Sequence before reference**, and the order is the bug it fixes. `columns`,
    # `dimensions`, `bound_terms` and `related_terms` are *lists of* references, and
    # classifying them as `ref` gave them a scalar's operators — so `dimensions:len_gte:3`
    # came back in `unknown_where` on a field where "how many does it have" is the obvious
    # question. Measured: it was silently unapplicable for all 399 metrics.
    if "tuple" in annotation or "list" in annotation or "Sequence" in annotation:
        return FieldKind.list
    if field.name in _REF_FIELDS:
        return FieldKind.ref
    if "bool" in annotation:
        return FieldKind.boolean
    if "int" in annotation or "float" in annotation:
        return FieldKind.number
    # An Enum-typed field is a closed vocabulary and deserves `one_of`, which is the whole
    # reason `kind` exists rather than "string or not".
    for name in ("LogicalType", "ColumnRole", "Cardinality", "Complexity", "ReliabilityStatus", "TermRelation"):
        if name in annotation:
            return FieldKind.enum
    return FieldKind.string


def columns_for(asset_type: AssetType, cls: type) -> list[dict[str, Any]]:
    """The filterable columns of one asset type, derived from its dataclass.

    ``identifier`` marks the fields the register declares as this type's identifier — the
    ones a reader searches by. The UI puts those first; nothing here depends on the order.
    """
    policy = ASSET_REGISTER[asset_type]
    out: list[dict[str, Any]] = []
    for field in dataclasses.fields(cls):
        kind = _kind_of(field)
        out.append(
            {
                "name": field.name,
                "kind": kind.value,
                "ops": list(OPS_BY_KIND[kind]),
                # A block is a nested object: sorting rows by it would order them by a
                # repr, which is a stable-looking arbitrary order.
                "sortable": kind not in (FieldKind.block, FieldKind.list),
                "identifier": field.name in policy.identifier_fields,
            }
        )
    return out


def parse_where(raw: Iterable[str]) -> tuple[list[tuple[str, str, str]], list[str]]:
    """``["schema:eq:airline"]`` → ``([("schema","eq","airline")], [])``.

    Splits on the first two colons only, so a value may contain them —
    ``id:eq:beer_factory.customers`` and ``summary:contains:a:b`` both work. A malformed
    triple goes to the second list rather than being skipped.
    """
    parsed: list[tuple[str, str, str]] = []
    bad: list[str] = []
    for item in raw or ():
        text = str(item)
        parts = text.split(":", 2)
        if len(parts) != 3 or not parts[0] or not parts[1]:
            bad.append(text)
            continue
        parsed.append((parts[0], parts[1], parts[2]))
    return parsed, bad


def _value(asset: Any, name: str) -> Any:
    return getattr(asset, name, None)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(v) for v in value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return " ".join(_as_text(getattr(value, f.name, None)) for f in dataclasses.fields(value))
    return str(value)


def _matches(asset: Any, field: str, op: str, wanted: str) -> bool:
    value = _value(asset, field)
    if op == "present":
        truthy = bool(value) if not isinstance(value, (int, float)) or isinstance(value, bool) else True
        return truthy is (str(wanted).lower() not in ("false", "0", "no"))
    if op == "contains":
        return wanted.casefold() in _as_text(value).casefold()
    if op == "eq":
        return _as_text(value).casefold() == wanted.casefold()
    if op == "neq":
        return _as_text(value).casefold() != wanted.casefold()
    if op == "one_of":
        options = {p.strip().casefold() for p in wanted.split(",") if p.strip()}
        return _as_text(value).casefold() in options
    if op in ("gte", "lte"):
        try:
            left, right = float(value), float(wanted)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        return left >= right if op == "gte" else left <= right
    if op in ("len_gte", "len_lte"):
        try:
            size, bound = len(value or ()), int(wanted)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        return size >= bound if op == "len_gte" else size <= bound
    return False


def apply_where(
    assets: Sequence[Any],
    predicates: Sequence[tuple[str, str, str]],
    known: Mapping[str, Sequence[str]],
) -> tuple[list[Any], list[str]]:
    """``(rows that match every predicate, predicates that could not be applied)``.

    Conjunctive: every predicate must hold. Unknown field or unsupported operator goes to
    the second list **and is not applied** — reporting it is what stops a filtered-looking
    list from being unfiltered.
    """
    usable: list[tuple[str, str, str]] = []
    unknown: list[str] = []
    for field, op, wanted in predicates:
        ops = known.get(field)
        if ops is None or op not in ops:
            unknown.append(f"{field}:{op}:{wanted}")
            continue
        usable.append((field, op, wanted))
    rows = [asset for asset in assets if all(_matches(asset, field, op, wanted) for field, op, wanted in usable)]
    return rows, unknown


def sort_rows(rows: list[Any], sort: str | None, order: str) -> list[Any]:
    """Sort by one column, ``id`` as the tiebreak so a page is stable across requests.

    Without the tiebreak two rows equal on the sort key can swap between requests, and a
    paginated reader then sees one row twice and another never — which looks like missing
    data rather than an unstable sort.
    """
    reverse = str(order).lower() == "desc"
    if not sort:
        return sorted(rows, key=lambda a: str(getattr(a, "id", "")), reverse=reverse)
    return sorted(
        rows,
        key=lambda a: (_as_text(_value(a, sort)).casefold(), str(getattr(a, "id", ""))),
        reverse=reverse,
    )


def row_for(asset: Any, columns: Sequence[str] | None = None) -> dict[str, Any]:
    """One asset as a JSON-safe row. Nested blocks become their rendered text.

    Deliberately flat: the client filters and sorts on the same strings the server did, so
    a column shown and a column filtered are the same value.
    """
    fields = dataclasses.fields(asset)
    wanted = set(columns) if columns else None
    out: dict[str, Any] = {}
    for field in fields:
        if wanted is not None and field.name not in wanted:
            continue
        value = _value(asset, field.name)
        if isinstance(value, Enum):
            out[field.name] = value.value
        elif isinstance(value, (list, tuple)):
            out[field.name] = [_as_text(v) for v in value]
        elif dataclasses.is_dataclass(value) and not isinstance(value, type):
            out[field.name] = _as_text(value) or None
        else:
            out[field.name] = value
    out["asset_type"] = getattr(getattr(asset, "asset_type", None), "value", None)
    return out


def predicate_columns(on: str) -> set[tuple[str, str]]:
    """``(qualifier, column)`` pairs an ON clause names, casefolded. ``("", col)`` if bare.

    Parsed, not scanned. A substring test for the column's name would match ``id`` inside
    ``customer_id`` and match a table alias that happens to spell a column — and this decides
    which joins a column's detail panel claims to be part of, so a false positive is an
    assertion that a relationship exists.

    Returns an empty set for an unparseable clause rather than raising: the corpus is already
    loaded and one malformed predicate must not take a detail panel down. Mirrors
    :func:`~governed_bi.corpus.identity.on_digest`'s wrapping trick, which is the one place
    that knows how to get sqlglot to parse a bare ON clause.
    """
    if not isinstance(on, str) or not on.strip():
        return set()
    try:
        tree = sqlglot.parse_one(f"SELECT 1 FROM _left AS _l JOIN _right AS _r ON {on}", dialect="postgres")
    except SqlglotError:
        return set()
    join = tree.find(exp.Join)
    if join is None or join.args.get("on") is None:
        return set()
    return {
        (str(col.table or "").casefold(), str(col.name or "").casefold())
        for col in join.args["on"].find_all(exp.Column)
    }


def _boundary(
    *,
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    kept_ids: set[str],
) -> list[dict[str, Any]]:
    """Curated joins leaving the scope for **another namespace**, as navigable stubs.

    A cross-schema join executes (ADR 0005), so the far end of one is a place to go, not a
    warning — which is why this is a list of destinations and carries no severity.

    The scoped view has to say something about them or it misrepresents the corpus: a table
    whose only join crosses a namespace draws as isolated, and "isolated" is a claim about the
    schema rather than about the window. The client used to synthesise these itself from the
    full graph; it can no longer do so once it trusts the engine's own scoping, and losing
    them silently would trade one wrong picture for another.

    A qualifying edge has exactly one endpoint in scope and is a **join** — it carries an ``on``
    predicate, or its relation says so. Semantic references (a term grounding a column) are
    excluded: they are not somewhere you can navigate to and back.
    """
    by_id = {str(node["id"]): node for node in nodes}

    def label_of(node: Mapping[str, Any] | None) -> str:
        if node is None:
            return ""
        return str(node.get("label") or node.get("physical_name") or node.get("id") or "")

    out: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in edges:
        left, right = str(edge.get("source")), str(edge.get("target"))
        inside_left = left in kept_ids
        if inside_left == (right in kept_ids):  # both in, or both out — not a crossing
            continue
        inside_id, outside_id = (left, right) if inside_left else (right, left)
        inside, outside = by_id.get(inside_id), by_id.get(outside_id)
        if outside is None:
            continue
        other_schema = outside.get("schema")
        if not other_schema or other_schema == (inside or {}).get("schema"):
            continue  # same namespace, or the far end has none to compare
        predicate = str(edge.get("on") or "")
        if not predicate:
            if str(edge.get("relation") or "") != "join":
                continue
            # The semantic graph puts the ON clause in the join asset's label, so a join-kind
            # endpoint is where the predicate is when the edge itself has no `on`.
            for candidate in (outside, inside):
                if candidate is not None and str(candidate.get("kind") or "") == "join":
                    predicate = label_of(candidate)
                    break
        confidence = edge.get("confidence")
        # One stub per (in-scope table, far table): several joins between the same pair is the
        # normal case, and it is one destination.
        out.setdefault(
            (inside_id, outside_id),
            {
                "id": f"boundary_{inside_id}__{outside_id}",
                "in_scope_table": inside_id,
                "other_schema": str(other_schema),
                "other_table_id": outside_id,
                "other_label": label_of(outside),
                "on": predicate,
                "cardinality": edge.get("cardinality"),
                "confidence": confidence,
                "low_confidence": bool(confidence is not None and float(confidence) < 0.5),
            },
        )
    return [out[key] for key in sorted(out)]


def subgraph(
    *,
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    schema: str | None = None,
    focus: str | None = None,
    radius: int = 1,
    kinds: Sequence[str] | None = None,
    node_budget: int = DEFAULT_NODE_BUDGET,
) -> dict[str, Any]:
    """A bounded relationship view, and a ``meta`` that says what it cut. ADR 0009 D2.

    Order matters. ``schema`` and ``kinds`` narrow the candidate set; ``focus`` then walks
    outward over the *edges of that candidate set* for ``radius`` hops; the budget is applied
    last, breadth-first from the focus, so what survives truncation is what is nearest rather
    than whatever sorted first.

    ``truncated`` and ``dropped`` are load-bearing. A view that silently renders 120 of 656
    nodes reads as complete coverage, and this repository has published a number on top of
    that shape. If the budget bites, the caller is told.
    """
    wanted_kinds = {str(k) for k in kinds} if kinds else None
    keep = [
        node
        for node in nodes
        if (schema is None or str(node.get("schema") or "") == schema)
        and (wanted_kinds is None or str(node.get("kind") or "") in wanted_kinds)
    ]
    keep_ids = {str(node["id"]) for node in keep}

    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        left, right = str(edge.get("source")), str(edge.get("target"))
        if left in keep_ids and right in keep_ids:
            adjacency.setdefault(left, set()).add(right)
            adjacency.setdefault(right, set()).add(left)

    if focus and str(focus) in keep_ids:
        frontier = {str(focus)}
        reached = set(frontier)
        for _ in range(max(0, int(radius))):
            nxt: set[str] = set()
            for node_id in frontier:
                nxt |= adjacency.get(node_id, set()) - reached
            if not nxt:
                break
            reached |= nxt
            frontier = nxt
        ordered = [n for n in keep if str(n["id"]) in reached]
        # Breadth order from the focus, so truncation keeps the near neighbourhood.
        distance = {str(focus): 0}
        frontier = {str(focus)}
        step = 0
        while frontier:
            step += 1
            nxt = set()
            for node_id in frontier:
                for neighbour in adjacency.get(node_id, set()):
                    if neighbour not in distance:
                        distance[neighbour] = step
                        nxt.add(neighbour)
            frontier = nxt
        ordered.sort(key=lambda n: (distance.get(str(n["id"]), 10**6), str(n["id"])))
    else:
        # No focus means no natural centre — but ordering by id spends the entire budget on an
        # alphabetical prefix, and an alphabetical prefix rarely contains *both* ends of any
        # edge. Measured on the pooled lake: 150 of 7,977 nodes and **zero** edges, which the
        # client drew as a single 18,000px column of unrelated cards. A relationship view that
        # shows no relationships is worse than a truncated one, and it reads as "this corpus
        # has none".
        #
        # So grow neighbourhoods instead: seed at the best-connected node, take its component
        # breadth-first, then move to the next unvisited seed, until the budget runs out. The
        # budget then buys a connected picture. Isolated nodes have degree 0 and so sort last,
        # which makes an edgeless corpus degrade to the old id order rather than to nothing.
        by_id = {str(n["id"]): n for n in keep}
        degree = {node_id: len(adjacency.get(node_id, ())) for node_id in by_id}
        rank = sorted(by_id, key=lambda i: (-degree[i], i))
        ordered = []
        seen: set[str] = set()
        for seed in rank:
            if seed in seen:
                continue
            seen.add(seed)
            queue = deque([seed])
            while queue:
                node_id = queue.popleft()
                ordered.append(by_id[node_id])
                neighbours = sorted(adjacency.get(node_id, ()), key=lambda i: (-degree.get(i, 0), i))
                for neighbour in neighbours:
                    if neighbour not in seen:
                        seen.add(neighbour)
                        queue.append(neighbour)

    budget = max(1, int(node_budget or DEFAULT_NODE_BUDGET))
    kept = ordered[:budget]
    dropped = len(ordered) - len(kept)
    kept_ids = {str(node["id"]) for node in kept}
    kept_edges = [edge for edge in edges if str(edge.get("source")) in kept_ids and str(edge.get("target")) in kept_ids]
    return {
        "nodes": list(kept),
        "edges": kept_edges,
        "boundary": _boundary(nodes=nodes, edges=edges, kept_ids=kept_ids),
        "meta": {
            "n_nodes": len(kept),
            "n_edges": len(kept_edges),
            "n_total_nodes": len(nodes),
            "n_matched_nodes": len(ordered),
            "truncated": dropped > 0,
            "dropped": dropped,
            "node_budget": budget,
            # The scope **as applied**, and it must be complete, because the client compares it
            # field-for-field against what it asked for and re-scopes the payload itself when
            # they differ. `node_budget` was missing from here while sitting one level up in
            # `meta`, so that comparison could never succeed: the client re-truncated every
            # response and then rebuilt `meta` from its own pass, overwriting
            # `truncated: True, dropped: 7827` with `false`/`0`. The budget's own honesty
            # depends on this key — ADR 0009 D2 exists to stop a bounded view reading as a
            # complete one, and a `dropped` the client discards is exactly that.
            "scope": {
                "schema": schema,
                "focus": focus,
                "radius": int(radius),
                "kinds": sorted(wanted_kinds) if wanted_kinds else None,
                "node_budget": budget,
            },
        },
    }
