"""Server-side graph scoping (D15 Phase 2).

Applies ``schema`` / ``focus`` / ``radius`` / ``node_budget`` / ``kinds`` to the
full ER and knowledge-graph views from :mod:`presenter`, producing the
``boundary`` + ``meta`` envelope the UI trusts via ``engineScopeMatches``.

Truncation order (D15 Q8): BFS distance from focus, then discovery-edge
confidence desc, then id asc. Defaults match the UI client
(``DEFAULT_ER_BUDGET=60``, ``DEFAULT_KG_BUDGET=150``, focus radius default 1).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .presenter import (
    BoundaryEdge,
    GraphMeta,
    GraphScopeApplied,
    KnowledgeGraphNode,
    KnowledgeGraphView,
    SchemaGraphEdge,
    SchemaGraphNode,
    SchemaGraphView,
)

DEFAULT_ER_BUDGET = 60
DEFAULT_KG_BUDGET = 150
MAX_ER_NODE_BUDGET = 60
MAX_KG_NODE_BUDGET = 150
LOW_CONFIDENCE = 0.7


@dataclass(frozen=True)
class ScopeRequest:
    """Parsed query params for a scoped graph request."""

    schema: str | None = None
    focus: str | None = None
    radius: int | None = None
    node_budget: int | None = None
    kinds: frozenset[str] | None = None  # KG only

    def narrowing(self) -> bool:
        return bool(self.schema or self.focus)


def _cap_budget(requested: int | None, *, default: int | None, ceiling: int) -> int | None:
    """Effective budget: request or narrowing default, hard-capped."""
    if requested is None and default is None:
        return None
    raw = default if requested is None else requested
    assert raw is not None
    return min(max(1, raw), ceiling)


def _adjacency(edges: list) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e.source, set()).add(e.target)
        adj.setdefault(e.target, set()).add(e.source)
    return adj


def _edge_confidence_map(edges: list) -> dict[tuple[str, str], float]:
    """Undirected pair → best confidence (null → 0.0)."""
    conf: dict[tuple[str, str], float] = {}
    for e in edges:
        key = (e.source, e.target) if e.source <= e.target else (e.target, e.source)
        c = e.confidence if e.confidence is not None else 0.0
        conf[key] = max(conf.get(key, 0.0), c)
    return conf


def _pair_conf(conf: dict[tuple[str, str], float], a: str, b: str) -> float:
    key = (a, b) if a <= b else (b, a)
    return conf.get(key, 0.0)


def _bfs_ordered(
    focus: str,
    adjacency: dict[str, set[str]],
    edge_conf: dict[tuple[str, str], float],
    radius: int,
) -> dict[str, tuple[int, float]]:
    """BFS from focus within ``radius`` hops.

    Returns id → (distance, discovery_edge_confidence). Neighbor expansion at
    each hop is ordered by confidence desc then id asc (D15 Q8).
    """
    if focus not in adjacency and focus:  # isolated / unknown focus
        return {focus: (0, 1.0)} if focus else {}
    dist: dict[str, tuple[int, float]] = {focus: (0, 1.0)}
    frontier = [focus]
    for d in range(1, max(radius, 0) + 1):
        if not frontier:
            break
        next_frontier: list[str] = []
        # Collect candidates from this layer, then sort for deterministic discovery.
        candidates: list[tuple[float, str, str]] = []  # (-conf, nbr_id, from_id)
        for u in frontier:
            for nbr in adjacency.get(u, ()):
                if nbr in dist:
                    continue
                candidates.append((-_pair_conf(edge_conf, u, nbr), nbr, u))
        candidates.sort()
        seen_layer: set[str] = set()
        for neg_c, nbr, u in candidates:
            if nbr in dist or nbr in seen_layer:
                continue
            seen_layer.add(nbr)
            dist[nbr] = (d, -neg_c)
            next_frontier.append(nbr)
        frontier = next_frontier
    return dist


def _scope_meta(
    total_nodes: int,
    returned_nodes: int,
    total_edges: int,
    req: ScopeRequest,
    *,
    effective_budget: int | None,
    effective_radius: int | None,
) -> GraphMeta:
    return GraphMeta(
        total_nodes=total_nodes,
        returned_nodes=returned_nodes,
        total_edges=total_edges,
        truncated=returned_nodes < total_nodes,
        scope=GraphScopeApplied(
            schema=req.schema,
            focus=req.focus,
            radius=effective_radius,
            node_budget=effective_budget,
        ),
    )


def apply_er_scope(view: SchemaGraphView, *, req: ScopeRequest) -> SchemaGraphView:
    """Scope an ER (tables + joins) graph to ``req``."""
    narrowing = req.narrowing()
    default_budget = DEFAULT_ER_BUDGET if narrowing else None
    budget = _cap_budget(req.node_budget, default=default_budget, ceiling=MAX_ER_NODE_BUDGET)

    if not narrowing and budget is None:
        return view  # unscoped: leave bare {nodes, edges} for back-compat

    adj = _adjacency(view.edges)
    edge_conf = _edge_confidence_map(view.edges)
    node_by_id = {n.id: n for n in view.nodes}

    effective_radius: int | None = None
    distances: dict[str, tuple[int, float]] | None = None
    candidates = list(view.nodes)

    if req.schema is not None:
        candidates = [n for n in candidates if n.schema == req.schema]

    if req.focus is not None:
        effective_radius = req.radius if req.radius is not None else 1
        distances = _bfs_ordered(req.focus, adj, edge_conf, effective_radius)
        # Focus neighborhood overwrites schema-only candidate set (UI parity).
        candidates = [node_by_id[i] for i in distances if i in node_by_id]

    def sort_key(n: SchemaGraphNode) -> tuple:
        if distances is not None:
            d, c = distances.get(n.id, (10**9, 0.0))
            return (d, -c, n.id)
        return (0, 0.0, n.id)

    ordered = sorted(candidates, key=sort_key)
    total_candidates = len(ordered)
    limit = budget if budget is not None else total_candidates
    kept = ordered[:limit]
    kept_ids = {n.id for n in kept}

    in_scope: list[SchemaGraphEdge] = []
    boundary: list[BoundaryEdge] = []
    for e in view.edges:
        src_in = e.source in kept_ids
        tgt_in = e.target in kept_ids
        if src_in and tgt_in:
            in_scope.append(e)
        elif src_in or tgt_in:
            in_id = e.source if src_in else e.target
            out_id = e.target if src_in else e.source
            out = node_by_id.get(out_id)
            inn = node_by_id.get(in_id)
            if (
                out is not None
                and inn is not None
                and out.schema
                and out.schema != inn.schema
            ):
                boundary.append(
                    BoundaryEdge(
                        id=f"boundary_{e.id}",
                        in_scope_table=in_id,
                        other_schema=out.schema,
                        other_table_id=out.id,
                        other_label=out.physical_name,
                        on=e.on,
                        cardinality=e.cardinality,
                        confidence=e.confidence,
                        low_confidence=e.low_confidence,
                    )
                )

    kept_sorted = sorted(kept, key=lambda n: n.id)
    edges_sorted = sorted(in_scope, key=lambda e: e.id)
    meta = _scope_meta(
        total_candidates,
        len(kept_sorted),
        len(edges_sorted),
        req,
        effective_budget=budget,
        effective_radius=effective_radius,
    )
    # Echo the budget that was applied when narrowing (including default).
    if narrowing and budget is not None:
        meta = replace(
            meta,
            scope=replace(meta.scope, node_budget=budget) if meta.scope else None,
        )

    return SchemaGraphView(
        nodes=kept_sorted,
        edges=edges_sorted,
        boundary=sorted(boundary, key=lambda b: b.id),
        meta=meta,
    )


def apply_kg_scope(view: KnowledgeGraphView, *, req: ScopeRequest) -> KnowledgeGraphView:
    """Scope a knowledge graph to ``req`` (optional ``kinds`` pre-filter)."""
    narrowing = req.narrowing()
    default_budget = DEFAULT_KG_BUDGET if narrowing else None
    budget = _cap_budget(req.node_budget, default=default_budget, ceiling=MAX_KG_NODE_BUDGET)

    nodes = list(view.nodes)
    if req.kinds is not None:
        nodes = [n for n in nodes if n.kind in req.kinds]
        # Keep edges whose endpoints survive the kind filter.
        node_ids = {n.id for n in nodes}
        edges = [e for e in view.edges if e.source in node_ids and e.target in node_ids]
        view = KnowledgeGraphView(nodes=nodes, edges=edges)

    if not narrowing and budget is None:
        return view

    adj = _adjacency(view.edges)
    edge_conf = _edge_confidence_map(view.edges)
    node_by_id = {n.id: n for n in view.nodes}

    effective_radius: int | None = None
    distances: dict[str, tuple[int, float]] | None = None

    if req.focus is not None:
        effective_radius = req.radius if req.radius is not None else 1
        distances = _bfs_ordered(req.focus, adj, edge_conf, effective_radius)
        table_ids = set(distances.keys())
    elif req.schema is not None:
        table_ids = {n.id for n in view.nodes if n.schema == req.schema}
    else:
        table_ids = {n.id for n in view.nodes}

    keep = set(table_ids)
    if narrowing:
        for e in view.edges:
            if e.source in keep:
                keep.add(e.target)
            if e.target in keep:
                keep.add(e.source)

    def sort_key(n: KnowledgeGraphNode) -> tuple:
        if distances is not None:
            d, c = distances.get(n.id, (10**9, 0.0))
            return (d, -c, n.id)
        return (0, 0.0, n.id)

    ordered = sorted(
        (node_by_id[i] for i in keep if i in node_by_id),
        key=sort_key,
    )
    total_candidates = len(ordered)
    limit = budget if budget is not None else total_candidates
    kept = ordered[:limit]
    kept_ids = {n.id for n in kept}
    in_scope = [e for e in view.edges if e.source in kept_ids and e.target in kept_ids]

    boundary: list[BoundaryEdge] = []
    for node in view.nodes:
        if node.kind != "join":
            continue
        endpoints = [
            node_by_id[i]
            for i in adj.get(node.id, ())
            if i in node_by_id and node_by_id[i].kind == "table"
        ]
        if len(endpoints) != 2:
            continue
        x, y = endpoints
        x_in, y_in = x.id in kept_ids, y.id in kept_ids
        if x_in == y_in:
            continue
        inn, out = (x, y) if x_in else (y, x)
        if out.schema and out.schema != inn.schema:
            conf = node.confidence
            boundary.append(
                BoundaryEdge(
                    id=f"boundary_{node.id}",
                    in_scope_table=inn.id,
                    other_schema=out.schema,
                    other_table_id=out.id,
                    other_label=out.label,
                    on=node.label,
                    cardinality=None,
                    confidence=conf,
                    low_confidence=(conf if conf is not None else 1.0) < LOW_CONFIDENCE,
                )
            )

    kept_sorted = sorted(kept, key=lambda n: n.id)
    edges_sorted = sorted(in_scope, key=lambda e: e.id)
    meta = _scope_meta(
        total_candidates,
        len(kept_sorted),
        len(edges_sorted),
        req,
        effective_budget=budget,
        effective_radius=effective_radius,
    )
    if narrowing and budget is not None and meta.scope is not None:
        meta = replace(meta, scope=replace(meta.scope, node_budget=budget))

    return KnowledgeGraphView(
        nodes=kept_sorted,
        edges=edges_sorted,
        boundary=sorted(boundary, key=lambda b: b.id),
        meta=meta,
    )


def parse_kinds(raw: str | None) -> frozenset[str] | None:
    """Parse comma-separated ``kinds`` query param."""
    if raw is None or not raw.strip():
        return None
    parts = {p.strip() for p in raw.split(",") if p.strip()}
    return frozenset(parts) if parts else None
