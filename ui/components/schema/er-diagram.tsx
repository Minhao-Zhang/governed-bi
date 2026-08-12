"use client";

/**
 * Column-level ER "Relationships" diagram.
 *
 * Table-card nodes come from GET /schema/summary (lean column rows); FK edges come from
 * GET /graph (cardinality + the join predicate). Edges are anchored to the actual
 * participating COLUMNS by parsing the edge's `on` equality
 * ("table_b.a_id = table_a.id") and mapping physical names → node ids, with a
 * card-level fallback handle so an edge never silently vanishes. Layout is dagre
 * LR (see lib/graph-layout). Reliability is the only loud channel: suspect columns
 * amber, low-confidence FKs red-dashed, excluded tables/columns dimmed.
 *
 * D15: the ER graph self-fetches at the current `scope`. When the engine bounds
 * the result it hands back `meta.truncated` (→ an "expand budget" banner) and
 * cross-schema `boundary` stubs (→ a neutral, navigable off-canvas panel). Cross-
 * schema boundaries are rendered as normal affordances, never warnings — red stays
 * reserved for the reliability channel (low-confidence joins).
 */

import { useEffect, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Panel,
  Handle,
  Position,
  MarkerType,
  useNodesState,
  useEdgesState,
  type Node,
  type NodeHandle,
  type Edge,
  type NodeProps,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ArrowUpRight, KeyRound, Link2, TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { QueryState } from "@/components/common/query-state";
import { useFocusContext } from "@/components/schema/use-focus-context";
import { useCatalog, useErGraph, useSchemaSummary } from "@/hooks/queries";
import { layoutGraph, type LayoutEdge, type LayoutNode } from "@/lib/graph-layout";
import {
  annotateNodeSchemas,
  applyErGraphScope,
  DEFAULT_ER_BUDGET,
} from "@/lib/graph-scope";
import type {
  BoundaryEdge,
  ErGraph,
  ErGraphEdge,
  GraphSelection,
  LeanColumn,
  SchemaScope,
  TableSummary,
} from "@/lib/types";

const CARD_W = 248;
const HEADER_H = 40;
const ROW_H = 26;
const PAD_Y = 6;
const EDGE_STROKE = "#94a3b8";
const LOW_STROKE = "#c0392b";
/** Fallback node budget for the "expand" banner (mirrors the graph-scope default). */
const DEFAULT_BUDGET = DEFAULT_ER_BUDGET;
const BUDGET_STEP = 50;

const cardHeight = (t: TableSummary) => HEADER_H + t.columns.length * ROW_H + PAD_Y * 2;
const rowCenterY = (i: number) => HEADER_H + PAD_Y + i * ROW_H + ROW_H / 2;

const CARDINALITY_LABEL: Record<string, string> = {
  many_to_one: "N:1",
  one_to_many: "1:N",
  one_to_one: "1:1",
  many_to_many: "N:N",
};

/* ── Locked view contract ─────────────────────────────────────────────────── */

type GraphViewProps = {
  scope?: SchemaScope;
  onScopeChange?: (next: SchemaScope) => void;
  onSelect: (selection: GraphSelection) => void;
};

/* ── Custom table-card node ───────────────────────────────────────────────── */

type ErNodeData = { table: TableSummary };
type ErFlowNode = Node<ErNodeData, "erTable">;

/** Predefined handle bounds (edge anchoring is measurement-free) — a west target
 * + east source per column, plus card-level fallbacks. */
function nodeHandles(t: TableSummary): NodeHandle[] {
  const hs: NodeHandle[] = [
    { id: "t-__card", type: "target", position: Position.Left, x: 0, y: HEADER_H / 2, width: 1, height: 1 },
    { id: "s-__card", type: "source", position: Position.Right, x: CARD_W, y: HEADER_H / 2, width: 1, height: 1 },
  ];
  t.columns.forEach((c, i) => {
    const y = rowCenterY(i);
    hs.push({ id: `t-${c.physical_name}`, type: "target", position: Position.Left, x: 0, y, width: 1, height: 1 });
    hs.push({ id: `s-${c.physical_name}`, type: "source", position: Position.Right, x: CARD_W, y, width: 1, height: 1 });
  });
  return hs;
}

function ErTableCard({ data, selected }: NodeProps<ErFlowNode>) {
  const t = data.table;
  return (
    <div
      style={{ width: CARD_W }}
      className={cn(
        "relative cursor-pointer overflow-hidden rounded-md border bg-card text-left shadow-sm ring-1 ring-foreground/5 transition-shadow hover:shadow-md",
        selected && "ring-2 ring-ring",
        t.excluded && "border-dashed opacity-60",
      )}
    >
      {/* Card-level fallback handles (invisible). */}
      <Handle id="t-__card" type="target" position={Position.Left} className="opacity-0!" style={{ top: HEADER_H / 2 }} />
      <Handle id="s-__card" type="source" position={Position.Right} className="opacity-0!" style={{ top: HEADER_H / 2 }} />

      <div
        className="flex items-center justify-between gap-2 border-b bg-muted/40 px-2"
        style={{ height: HEADER_H }}
      >
        <span className="truncate font-mono text-sm font-semibold" title={t.physical_name}>
          {t.physical_name}
        </span>
        <span className="shrink-0 text-[0.65rem] text-muted-foreground">
          {t.row_count?.toLocaleString() ?? "—"} rows
        </span>
      </div>

      <div style={{ paddingTop: PAD_Y, paddingBottom: PAD_Y }}>
        {t.columns.map((col, i) => (
          <ErColumnRow key={col.physical_name} col={col} y={rowCenterY(i)} />
        ))}
      </div>
    </div>
  );
}

function ErColumnRow({ col, y }: { col: LeanColumn; y: number }) {
  const suspect = col.reliability === "suspect";
  return (
    <div
      className={cn(
        "relative flex items-center gap-1.5 px-2",
        suspect && "bg-tier-lineage/10",
        col.excluded && "opacity-50",
      )}
      style={{ height: ROW_H }}
    >
      {/* Column-level anchors (invisible), vertically aligned to this row. */}
      <Handle id={`t-${col.physical_name}`} type="target" position={Position.Left} className="opacity-0!" style={{ top: y }} />
      <Handle id={`s-${col.physical_name}`} type="source" position={Position.Right} className="opacity-0!" style={{ top: y }} />

      {col.role === "primary_key" ? (
        <KeyRound className="size-3 shrink-0 text-foreground" aria-label="primary key" />
      ) : col.role === "foreign_key" ? (
        <Link2 className="size-3 shrink-0 text-muted-foreground" aria-label="foreign key" />
      ) : (
        <span className="size-3 shrink-0" />
      )}
      <span className="truncate font-mono text-xs">{col.physical_name}</span>
      {suspect && <TriangleAlert className="size-3 shrink-0 text-tier-lineage" aria-label="suspect" />}
      <span className="ml-auto shrink-0 truncate font-mono text-[0.6rem] text-muted-foreground">
        {col.physical_type}
        {/* `=== false`, not falsy: `null` means the corpus never observed nullability, and
            printing ·NN for it would assert NOT NULL on a column nobody checked. */}
        {col.nullable === false ? " ·NN" : ""}
        {col.is_unique === true ? " ·U" : ""}
      </span>
    </div>
  );
}

const NODE_TYPES = { erTable: ErTableCard };

/* ── FK endpoint resolution ───────────────────────────────────────────────── */

function parseSide(s: string | undefined): { table: string; col: string } | null {
  const parts = (s ?? "").trim().split(".");
  return parts.length === 2 ? { table: parts[0].trim(), col: parts[1].trim() } : null;
}

/** Resolve each edge endpoint to a column on the participating node.
 * Match `on` table names against the edge's own source/target nodes only —
 * never a global physical_name map (duplicate names across namespaces collide). */
function resolveEndpoints(
  nodesById: Map<string, { physical_name: string }>,
  edge: ErGraphEdge,
) {
  const [lhs, rhs] = edge.on.split("=");
  const sides = [parseSide(lhs), parseSide(rhs)];
  const colFor = (nodeId: string): string | null => {
    const node = nodesById.get(nodeId);
    if (!node) return null;
    for (const side of sides) {
      if (side && side.table === node.physical_name) return side.col;
    }
    return null;
  };
  return { srcCol: colFor(edge.source), tgtCol: colFor(edge.target) };
}

/* ── View ─────────────────────────────────────────────────────────────────── */

export function ErDiagram({ scope, onScopeChange, onSelect }: GraphViewProps) {
  // /graph is the authoritative in-scope table + FK set; /schema/summary supplies the column
  // rows. It used to be the flat /schema dump — 936 KB to render cards that need a name, a
  // type, a role and three flags — and the only fields the lean catalog lacked were
  // `nullable` and `is_unique`, which it now carries. Two fields were the whole reason a
  // second projection of every table existed.
  // ONE resolved scope for the fetch AND the client-side fallback below. Fetching with a bare
  // `scope` while filtering with `withDefaultBudget(scope, …)` means the engine is never told
  // the budget, so its echoed `meta.scope` cannot equal the requested one, `engineScopeMatches`
  // always fails, and the client re-scopes every payload — discarding the engine's honest
  // `truncated`/`dropped` in the process. Same defect as in `knowledge-graph.tsx`.
  const resolvedScope = useMemo<SchemaScope>(
    () => ({
      ...(scope ?? {}),
      radius: scope?.radius ?? 1,
      nodeBudget: scope?.nodeBudget ?? DEFAULT_ER_BUDGET,
    }),
    [scope],
  );
  const er = useErGraph(resolvedScope);
  const { items: catalog } = useCatalog();
  // One fetch, `?schema=`-filtered when a namespace is active so a rail click does not pull
  // the whole catalog. `useSchemaSummary` keys its cache on that filter, so switching
  // namespaces is a different query rather than a refetch of everything.
  const summary = useSchemaSummary(scope);
  const tableDump = summary.data?.items ?? [];
  const schemaLoading = summary.isLoading;

  return (
    <QueryState
      query={er}
      isEmpty={(g) => g.nodes.length === 0}
      emptyMessage="No tables to diagram."
      skeleton={<Skeleton className="h-[70vh] w-full" />}
    >
      {(graph) => (
        <ErCanvas
          graph={graph}
          tableDump={tableDump}
          catalog={catalog}
          schemaLoading={schemaLoading}
          // The RESOLVED scope: the canvas filters with the same object the fetch was made
          // with, so the engine's echo can match it and its truncation survives.
          scope={resolvedScope}
          onScopeChange={onScopeChange}
          onSelect={onSelect}
        />
      )}
    </QueryState>
  );
}

function ErCanvas({
  graph,
  tableDump,
  catalog,
  schemaLoading,
  scope,
  onScopeChange,
  onSelect,
}: {
  graph: ErGraph;
  /** Column-detail source for card bodies/handles (full dump or ?schema=-filtered). */
  tableDump: TableSummary[];
  catalog: { id: string; namespace: string }[];
  schemaLoading: boolean;
  scope?: SchemaScope;
  onScopeChange?: (next: SchemaScope) => void;
  onSelect: (selection: GraphSelection) => void;
}) {
  const computed = useMemo(() => {
    // Cards are the scoped ER nodes joined to /schema for their columns; a node
    // without a matching TableSummary (schema still loading, or an unlisted table)
    // is skipped rather than fabricated.
    const dumpById = new Map(tableDump.map((t) => [t.id, t]));
    // Prefer catalog namespaces (lean summary); fall back to dump.schema.
    const namespaceById = new Map<string, string>([
      ...tableDump.map((t) => [t.id, t.schema] as const),
      ...catalog.map((it) => [it.id, it.namespace] as const),
    ]);
    const scopedGraph = applyErGraphScope(
      {
        ...graph,
        nodes: annotateNodeSchemas(graph.nodes, namespaceById),
      },
      scope,
    );

    const tables: TableSummary[] = scopedGraph.nodes
      .map((n) => dumpById.get(n.id))
      .filter((t): t is TableSummary => t !== undefined);

    const byId = new Map(tables.map((t) => [t.id, t]));
    const nodesById = new Map(scopedGraph.nodes.map((n) => [n.id, n]));

    const layoutNodes: LayoutNode[] = tables.map((t) => ({
      id: t.id,
      width: CARD_W,
      height: cardHeight(t),
    }));
    // Only edges whose endpoints both have a rendered card (defensive: keeps
    // React Flow from referencing a missing node).
    const scopedEdges = scopedGraph.edges.filter((e) => byId.has(e.source) && byId.has(e.target));
    const layoutEdges: LayoutEdge[] = scopedEdges.map((e) => ({ source: e.source, target: e.target }));
    const pos = layoutGraph(layoutNodes, layoutEdges, { direction: "LR", rankSep: 140 });

    const nodes: ErFlowNode[] = tables.map((t) => ({
      id: t.id,
      type: "erTable",
      position: pos[t.id] ?? { x: 0, y: 0 },
      width: CARD_W,
      height: cardHeight(t),
      handles: nodeHandles(t),
      data: { table: t },
    }));

    const edges: Edge[] = scopedEdges.map((e) => {
      const { srcCol, tgtCol } = resolveEndpoints(nodesById, e);
      const srcHasCol = srcCol && byId.get(e.source)?.columns.some((c) => c.physical_name === srcCol);
      const tgtHasCol = tgtCol && byId.get(e.target)?.columns.some((c) => c.physical_name === tgtCol);
      const low = e.low_confidence;
      // One line, possibly several declared relationships. The `on` shown is the first;
      // saying "+N" is the difference between a diagram that is incomplete and one that
      // is incomplete *and* says otherwise. Collapsing multiple joins into a silent single
      // edge is the v1 defect that cost 33 of 57 schemas a relationship.
      const extra = (e.n_relationships ?? 1) - 1;
      const cardinality = e.cardinality
        ? (CARDINALITY_LABEL[e.cardinality] ?? e.cardinality)
        : undefined;
      const label = extra > 0 ? `${cardinality ?? "join"} +${extra}` : cardinality;
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: srcHasCol ? `s-${srcCol}` : "s-__card",
        targetHandle: tgtHasCol ? `t-${tgtCol}` : "t-__card",
        type: "smoothstep",
        label,
        labelBgPadding: [4, 2] as [number, number],
        labelStyle: { fontSize: 10 },
        markerEnd: { type: MarkerType.ArrowClosed, color: low ? LOW_STROKE : EDGE_STROKE },
        style: {
          stroke: low ? LOW_STROKE : EDGE_STROKE,
          strokeWidth: low ? 2 : 1.5,
          strokeDasharray: low ? "6 4" : undefined,
        },
      } satisfies Edge;
    });

    return { nodes, edges, boundary: scopedGraph.boundary ?? [], meta: scopedGraph.meta };
  }, [graph, tableDump, catalog, scope]);

  const [nodes, setNodes, onNodesChange] = useNodesState<ErFlowNode>(computed.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(computed.edges);

  useEffect(() => {
    setNodes(computed.nodes);
    setEdges(computed.edges);
  }, [computed, setNodes, setEdges]);

  const handleNodeClick: NodeMouseHandler<ErFlowNode> = (_e, node) =>
    onSelect({ id: node.id, kind: "table", label: node.data.table.physical_name });

  // Focus+context: hovering a table dims everything outside its FK neighborhood.
  // The edge path, its label <text>, and the label bg <rect> are separate elements,
  // so dim all three (via style/labelStyle/labelBgStyle) — otherwise the "N:1"
  // label stays bright while its edge fades.
  const focus = useFocusContext(edges);
  const { dimNode, dimEdge } = focus;
  const shownNodes = useMemo(
    () => nodes.map((n) => ({ ...n, style: { ...n.style, opacity: dimNode(n.id) ? 0.25 : 1 } })),
    [nodes, dimNode],
  );
  const shownEdges = useMemo(
    () =>
      edges.map((e) => {
        const o = dimEdge(e.source, e.target) ? 0.12 : 1;
        return {
          ...e,
          style: { ...e.style, opacity: o },
          labelStyle: { ...(e.labelStyle ?? {}), opacity: o },
          labelBgStyle: { ...(e.labelBgStyle ?? {}), opacity: o },
        };
      }),
    [edges, dimEdge],
  );

  const meta = computed.meta;
  // `dropped` rather than a subtraction: the engine already knows how many nodes the budget
  // cut, and re-deriving it here would be a second answer that disagrees whenever the scope
  // filtered nodes out before the budget ran (ADR 0009 D2).
  const hiddenCount = meta?.dropped ?? 0;
  const boundary = computed.boundary;

  const expandBudget = () =>
    onScopeChange?.({ ...scope, nodeBudget: (scope?.nodeBudget ?? DEFAULT_BUDGET) + BUDGET_STEP });

  // Cross-schema boundary → navigate: narrow scope to the other schema focused on
  // the target table, and lift the selection so the detail sheet opens.
  const navigateBoundary = (b: BoundaryEdge) => {
    onScopeChange?.({ schema: b.other_schema, focus: b.other_table_id });
    onSelect({ id: b.other_table_id, kind: "table", label: b.other_label });
  };

  // While column dump is still loading for the active scope, prefer a skeleton
  // over an empty canvas of nodes that couldn't join yet.
  if (schemaLoading && computed.nodes.length === 0) {
    return <Skeleton className="h-[70vh] w-full" />;
  }

  return (
    <div className="h-[70vh] w-full rounded-md border bg-card">
      <ReactFlow<ErFlowNode, Edge>
        nodes={shownNodes}
        edges={shownEdges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onNodeMouseEnter={(_e, n) => focus.focus(n.id)}
        onNodeMouseLeave={() => focus.clear()}
        nodesConnectable={false}
        onlyRenderVisibleElements
        defaultViewport={{ x: 0, y: 0, zoom: 0.75 }}
        minZoom={0.4}
        maxZoom={1.5}
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        {/* Controls includes a manual fit-view button — no auto-fit-to-everything. */}
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable className="bg-muted!" />

        <Panel position="bottom-left">
          <ErLegend />
        </Panel>

        {meta?.truncated && hiddenCount > 0 && (
          <Panel position="top-center">
            <button
              type="button"
              onClick={expandBudget}
              className="rounded-full border bg-card/95 px-3 py-1 text-xs font-medium shadow-sm backdrop-blur transition-colors hover:bg-muted"
            >
              {hiddenCount} more — expand
            </button>
          </Panel>
        )}

        {boundary.length > 0 && (
          <Panel position="top-right">
            <div className="w-60 max-h-[60vh] space-y-1.5 overflow-auto rounded-md border bg-card/95 p-2 shadow-sm backdrop-blur">
              <div className="px-1 text-[0.65rem] font-semibold uppercase tracking-wide text-muted-foreground">
                Related tables
              </div>
              {boundary.map((b) => (
                <button
                  key={b.id}
                  type="button"
                  onClick={() => navigateBoundary(b)}
                  className="group flex w-full flex-col gap-0.5 rounded-md border border-transparent px-2 py-1.5 text-left transition-colors hover:border-border hover:bg-muted"
                >
                  <div className="flex items-center gap-1.5">
                    <span className="truncate font-mono text-xs font-medium" title={b.other_label}>
                      {b.other_label}
                    </span>
                    <Badge variant="outline" className="shrink-0 text-[0.6rem] font-normal">
                      {b.other_schema}
                    </Badge>
                    <ArrowUpRight className="ml-auto size-3 shrink-0 text-muted-foreground transition-colors group-hover:text-foreground" />
                  </div>
                  <span className="truncate font-mono text-[0.6rem] text-muted-foreground" title={b.on}>
                    {b.on}
                  </span>
                </button>
              ))}
            </div>
          </Panel>
        )}
      </ReactFlow>
    </div>
  );
}

/** Key to the diagram's glyphs and reliability channel. Kept compact in a
 * corner so the audit signals (suspect columns, low-confidence FKs) are legible
 * without hovering. */
function ErLegend() {
  return (
    <div className="space-y-1 rounded-md border bg-card/95 px-2.5 py-2 text-[0.65rem] text-muted-foreground shadow-sm backdrop-blur">
      <div className="flex items-center gap-1.5">
        <KeyRound className="size-3 shrink-0 text-foreground" aria-hidden />
        <span>primary key</span>
      </div>
      <div className="flex items-center gap-1.5">
        <Link2 className="size-3 shrink-0 text-muted-foreground" aria-hidden />
        <span>foreign key</span>
      </div>
      <div className="flex items-center gap-1.5">
        <TriangleAlert className="size-3 shrink-0 text-tier-lineage" aria-hidden />
        <span>suspect column</span>
      </div>
      <div className="flex items-center gap-1.5">
        <svg width="18" height="6" viewBox="0 0 18 6" aria-hidden className="shrink-0">
          <line
            x1="0"
            y1="3"
            x2="18"
            y2="3"
            stroke={LOW_STROKE}
            strokeWidth="1.5"
            strokeDasharray="4 3"
          />
        </svg>
        <span>low-confidence join</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-foreground">N:1</span>
        <span>cardinality</span>
      </div>
    </div>
  );
}
