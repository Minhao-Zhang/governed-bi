/**
 * Apply a `SchemaScope` to ER / knowledge-graph payloads.
 *
 * Used by mock mode to simulate a D15 engine, and by the live UI as a client
 * fallback when the attached engine advertises `can_scope` but still returns
 * the unscoped corpus (no matching `meta.scope`, nodes missing namespace).
 * Truncation order matches D15 Q8: BFS distance from focus, then id asc.
 */

import type {
  BoundaryEdge,
  ErGraph,
  ErGraphEdge,
  GraphMeta,
  KnowledgeGraph,
  SchemaScope,
} from "@/lib/types";

export const DEFAULT_ER_BUDGET = 60;
export const DEFAULT_KG_BUDGET = 150;

function scopeMeta(
  totalNodesInCorpus: number,
  matchedNodes: number,
  returnedNodes: number,
  totalEdges: number,
  scope: SchemaScope | undefined,
): GraphMeta {
  const dropped = matchedNodes - returnedNodes;
  return {
    // Engine field names (ADR 0009 D2). This builder is the *fallback* for an engine that
    // cannot scope, so it must produce the same shape the scoping engine does — otherwise
    // one of the two paths renders a truncation banner and the other cannot.
    //
    // `n_total_nodes` and `n_matched_nodes` are read from separate arguments rather than
    // both from one count: "how big is the corpus" and "how many matched this scope" are
    // different numbers, and the "showing X of Y in scope" line needs the second one.
    // Collapsing them makes that line report the corpus size on every scoped view.
    n_nodes: returnedNodes,
    n_edges: totalEdges,
    n_total_nodes: totalNodesInCorpus,
    n_matched_nodes: matchedNodes,
    truncated: dropped > 0,
    dropped,
    node_budget: scope?.nodeBudget ?? returnedNodes,
    scope: scope
      ? {
          schema: scope.schema ?? null,
          focus: scope.focus ?? null,
          radius: scope.radius ?? null,
          node_budget: scope.nodeBudget ?? null,
        }
      : undefined,
  };
}

/** True when the engine's `meta.scope` already matches what the UI requested. */
export function engineScopeMatches(
  meta: GraphMeta | null | undefined,
  scope: SchemaScope | undefined,
): boolean {
  const applied = meta?.scope;
  if (!applied) return false;
  const requested = scope ?? {};
  return (
    (applied.schema ?? null) === (requested.schema ?? null) &&
    (applied.focus ?? null) === (requested.focus ?? null) &&
    (applied.radius ?? null) === (requested.radius ?? null) &&
    (applied.node_budget ?? null) === (requested.nodeBudget ?? null)
  );
}

/** BFS distance from a focus id over an undirected view of the edges. */
function bfsDistances(
  focus: string,
  adjacency: Map<string, Set<string>>,
  radius: number,
): Map<string, number> {
  const dist = new Map<string, number>([[focus, 0]]);
  let frontier = [focus];
  for (let d = 1; d <= radius && frontier.length; d++) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const nbr of adjacency.get(id) ?? []) {
        if (!dist.has(nbr)) {
          dist.set(nbr, d);
          next.push(nbr);
        }
      }
    }
    frontier = next;
  }
  return dist;
}

/** Fill missing node `schema` from an id→namespace map (catalog /schema dump). */
export function annotateNodeSchemas<T extends { id: string; schema?: string | null }>(
  nodes: T[],
  namespaceById: Map<string, string>,
): T[] {
  return nodes.map((n) => {
    if (n.schema) return n;
    const ns = namespaceById.get(n.id);
    return ns ? { ...n, schema: ns } : n;
  });
}

/**
 * Effective scope for client-side graph filtering: always carries a node budget
 * so "All schemas" cannot lay out the entire corpus.
 */
export function withDefaultBudget(
  scope: SchemaScope | undefined,
  fallback: number,
): SchemaScope {
  return {
    ...(scope ?? {}),
    nodeBudget: scope?.nodeBudget ?? fallback,
  };
}

/* ── ER graph (table↔table) ──────────────────────────────────────────────── */

export function applyErGraphScope(base: ErGraph, scope: SchemaScope | undefined): ErGraph {
  // Trust the engine when it already scoped to the same request (avoid double-truncation).
  if (engineScopeMatches(base.meta, scope)) {
    return base;
  }

  const narrowing = !!(scope?.schema || scope?.focus);
  const budget = scope?.nodeBudget ?? (narrowing ? DEFAULT_ER_BUDGET : undefined);

  if (!narrowing && budget == null) {
    return {
      ...base,
      boundary: base.boundary ?? [],
      meta:
        base.meta ??
        scopeMeta(base.nodes.length, base.nodes.length, base.nodes.length, base.edges.length, scope),
    };
  }

  const nodeById = new Map(base.nodes.map((n) => [n.id, n]));
  const adjacency = new Map<string, Set<string>>();
  for (const e of base.edges) {
    if (!adjacency.has(e.source)) adjacency.set(e.source, new Set());
    if (!adjacency.has(e.target)) adjacency.set(e.target, new Set());
    adjacency.get(e.source)!.add(e.target);
    adjacency.get(e.target)!.add(e.source);
  }

  // 1. Candidate node set from schema filter and/or focus neighborhood.
  let candidates = base.nodes;
  if (scope?.schema) candidates = candidates.filter((n) => n.schema === scope.schema);

  let distances: Map<string, number> | null = null;
  if (scope?.focus) {
    distances = bfsDistances(scope.focus, adjacency, scope.radius ?? 1);
    candidates = base.nodes.filter((n) => distances!.has(n.id));
  }

  // 2. Deterministic order + node_budget truncation.
  const limit = budget ?? candidates.length;
  const ordered = [...candidates].sort((a, b) => {
    if (distances) {
      const da = distances.get(a.id) ?? Infinity;
      const db = distances.get(b.id) ?? Infinity;
      if (da !== db) return da - db;
    }
    return a.id.localeCompare(b.id);
  });
  const kept = ordered.slice(0, limit);
  const keptIds = new Set(kept.map((n) => n.id));

  // 3. In-scope edges + boundary edges (one endpoint in scope, one out).
  const inScopeEdges: ErGraphEdge[] = [];
  const boundary: BoundaryEdge[] = [];
  for (const e of base.edges) {
    const srcIn = keptIds.has(e.source);
    const tgtIn = keptIds.has(e.target);
    if (srcIn && tgtIn) {
      inScopeEdges.push(e);
    } else if (srcIn || tgtIn) {
      const inId = srcIn ? e.source : e.target;
      const outId = srcIn ? e.target : e.source;
      const out = nodeById.get(outId);
      const inNode = nodeById.get(inId);
      // Only a crossing into a *different* schema is a boundary worth showing.
      if (out && inNode && out.schema && out.schema !== inNode.schema) {
        boundary.push({
          id: `boundary_${e.id}`,
          in_scope_table: inId,
          other_schema: out.schema,
          other_table_id: out.id,
          other_label: out.physical_name,
          on: e.on,
          cardinality: e.cardinality,
          confidence: e.confidence,
          low_confidence: e.low_confidence,
        });
      }
    }
  }

  return {
    nodes: kept,
    edges: inScopeEdges,
    boundary,
    meta: scopeMeta(base.nodes.length, candidates.length, kept.length, inScopeEdges.length, scope),
  };
}

/* ── Knowledge graph (assets of every kind) ──────────────────────────────── */

export function applyKnowledgeGraphScope(
  base: KnowledgeGraph,
  scope: SchemaScope | undefined,
): KnowledgeGraph {
  if (engineScopeMatches(base.meta, scope)) {
    return base;
  }

  const narrowing = !!(scope?.schema || scope?.focus);
  const budget = scope?.nodeBudget ?? (narrowing ? DEFAULT_KG_BUDGET : undefined);

  if (!narrowing && budget == null) {
    return {
      ...base,
      boundary: base.boundary ?? [],
      meta:
        base.meta ??
        scopeMeta(base.nodes.length, base.nodes.length, base.nodes.length, base.edges.length, scope),
    };
  }

  const nodeById = new Map(base.nodes.map((n) => [n.id, n]));
  const adjacency = new Map<string, Set<string>>();
  for (const e of base.edges) {
    if (!adjacency.has(e.source)) adjacency.set(e.source, new Set());
    if (!adjacency.has(e.target)) adjacency.set(e.target, new Set());
    adjacency.get(e.source)!.add(e.target);
    adjacency.get(e.target)!.add(e.source);
  }

  // Table nodes carry a schema; asset nodes (metric/term/join/…) don't, so an
  // asset is in scope when it connects to an in-scope table.
  let tableIds: Set<string>;
  let distances: Map<string, number> | null = null;
  if (scope?.focus) {
    distances = bfsDistances(scope.focus, adjacency, scope.radius ?? 1);
    tableIds = new Set([...distances.keys()]);
  } else if (scope?.schema) {
    tableIds = new Set(
      base.nodes.filter((n) => n.schema && n.schema === scope.schema).map((n) => n.id),
    );
  } else {
    tableIds = new Set(base.nodes.map((n) => n.id));
  }

  const keep = new Set<string>(tableIds);
  // Pull in connected asset nodes (one hop) so the neighborhood stays legible.
  if (narrowing) {
    for (const e of base.edges) {
      if (keep.has(e.source)) keep.add(e.target);
      if (keep.has(e.target)) keep.add(e.source);
    }
  }

  const limit = budget ?? keep.size;
  const ordered = [...keep]
    .map((id) => nodeById.get(id))
    .filter((n): n is NonNullable<typeof n> => Boolean(n))
    .sort((a, b) => {
      if (distances) {
        const da = distances.get(a.id) ?? Infinity;
        const db = distances.get(b.id) ?? Infinity;
        if (da !== db) return da - db;
      }
      return a.id.localeCompare(b.id);
    });
  const kept = ordered.slice(0, limit);
  const keptIds = new Set(kept.map((n) => n.id));
  const inScopeEdges = base.edges.filter((e) => keptIds.has(e.source) && keptIds.has(e.target));

  // Boundary: a join node bridging an in-scope table and an out-of-scope table
  // in a different schema (the navigable cross-schema case).
  const boundary: BoundaryEdge[] = [];
  for (const node of base.nodes) {
    if (node.kind !== "join") continue;
    const endpoints = [...(adjacency.get(node.id) ?? [])]
      .map((id) => nodeById.get(id))
      .filter((n): n is NonNullable<typeof n> => Boolean(n) && n!.kind === "table");
    if (endpoints.length !== 2) continue;
    const [x, y] = endpoints;
    const xIn = keptIds.has(x.id);
    const yIn = keptIds.has(y.id);
    if (xIn === yIn) continue; // both in or both out — not a crossing
    const inNode = xIn ? x : y;
    const out = xIn ? y : x;
    if (out.schema && out.schema !== inNode.schema) {
      boundary.push({
        id: `boundary_${node.id}`,
        in_scope_table: inNode.id,
        other_schema: out.schema,
        other_table_id: out.id,
        other_label: out.label,
        on: node.label,
        cardinality: null,
        confidence: node.confidence ?? null,
        low_confidence: (node.confidence ?? 1) < 0.7,
      });
    }
  }

  return {
    nodes: kept,
    edges: inScopeEdges,
    boundary,
    meta: scopeMeta(base.nodes.length, keep.size, kept.length, inScopeEdges.length, scope),
  };
}

/* ── /schema/summary derivation (mock + client belt-and-suspenders) ──────── */

export function filterSummaryItems<T extends { schema: string }>(
  items: T[],
  scope: { schema?: string; limit?: number; offset?: number } | undefined,
): { total: number; items: T[] } {
  let rows = items;
  if (scope?.schema) rows = rows.filter((r) => r.schema === scope.schema);
  const total = rows.length;
  const offset = scope?.offset ?? 0;
  const limit = scope?.limit ?? rows.length;
  return { total, items: rows.slice(offset, offset + limit) };
}
