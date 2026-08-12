"use client";

/**
 * /schema — a search-first shell over the semantic layer. A search omnibox and a
 * single-level namespace rail NARROW a shared `SchemaScope`; the three self-fetching
 * views (ER relationships, semantic knowledge graph, plain table browser) all render
 * that scope, and a shared `GraphSelection` opens the detail sheet.
 *
 * D15: with an empty scope the graphs show the whole (budget-bounded) corpus; the
 * rail + search narrow it. Every scope-aware hook falls back to today's flat
 * behavior when the backend can't scope, so this page runs unchanged on either engine.
 */

import { useMemo, useState } from "react";

import { useCapabilities, useCatalog } from "@/hooks/queries";
import { groupByNamespace, type NamespaceRollup } from "@/lib/catalog";
import { cn } from "@/lib/utils";
import { PageShell } from "@/components/layout/page-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ErDiagram } from "@/components/schema/er-diagram";
import { KnowledgeGraph } from "@/components/schema/knowledge-graph";
import { NodeDetailSheet } from "@/components/schema/node-detail-sheet";
import { SchemaSearch } from "@/components/schema/schema-search";
import { TableBrowser } from "@/components/schema/table-browser";
import type { GraphSelection, SchemaScope } from "@/lib/types";

export default function SchemaPage() {
  // Read caps so the whole shell mounts the capability query once; the leaf views
  // and search box gate their own scoped/server behavior off the shared cache.
  useCapabilities();

  const [scope, setScope] = useState<SchemaScope>({});
  const [selection, setSelection] = useState<GraphSelection | null>(null);

  const handleSelect = (sel: GraphSelection) => setSelection(sel);

  // The rail lists the whole corpus's namespaces (independent of the active scope).
  const { items } = useCatalog();
  const namespaces = useMemo(() => groupByNamespace(items), [items]);

  // Resolve the focused table's physical name for the breadcrumb (best-effort).
  const focusLabel = useMemo(() => {
    if (!scope.focus) return null;
    return items.find((it) => it.id === scope.focus)?.physical_name ?? scope.focus;
  }, [items, scope.focus]);

  const scoped = !!scope.schema || !!scope.focus;

  return (
    <PageShell
      title="Schema"
      description="Relationships and the semantic layer as a knowledge graph."
    >
      {/* Two independently scrolling panes on desktop. The rail lists every
          namespace in the corpus — 67 on the data lake — so sharing one scroll
          container with the canvas meant reaching a schema near the bottom dragged
          the diagram off-screen with it. `h-full` resolves because PageShell's
          content box is a `flex-1` child of an `h-full` column (the same footing
          the chat transcript stands on), and `overflow-hidden` on the row keeps the
          outer container from scrolling once the panes own it.
          Below `lg` the panes stack, so heights are left alone and the page scrolls
          as one — a 56-wide rail pinned to a short viewport would be worse. */}
      <div className="flex flex-col gap-4 lg:h-full lg:flex-row lg:overflow-hidden">
        {/* Rail — single-level namespace picker. */}
        <aside className="w-full shrink-0 lg:h-full lg:w-56 lg:overflow-y-auto lg:pr-1">
          <nav className="flex flex-col gap-0.5">
            <RailButton
              label="All schemas"
              active={!scope.schema}
              onClick={() => setScope({})}
            />
            {namespaces.map((ns) => (
              <RailButton
                key={ns.namespace}
                label={ns.namespace}
                rollup={ns}
                active={scope.schema === ns.namespace}
                onClick={() => setScope({ schema: ns.namespace })}
              />
            ))}
          </nav>
        </aside>

        {/* Canvas — search, breadcrumb, tabbed views. `pb-1` keeps the last child's
            bottom edge off the scroll boundary, which otherwise clips its border. */}
        <div className="min-w-0 flex-1 space-y-4 lg:h-full lg:overflow-y-auto lg:pb-1">
          <SchemaSearch
            scope={scope}
            onScopeChange={setScope}
            onSelect={handleSelect}
          />

          <div className="flex min-h-7 items-center gap-2 text-sm text-muted-foreground">
            <span
              className={cn("cursor-pointer hover:text-foreground", !scoped && "text-foreground")}
              onClick={() => setScope({})}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") setScope({});
              }}
            >
              All schemas
            </span>
            {scope.schema && (
              <>
                <span aria-hidden>/</span>
                <span className="text-foreground">{scope.schema}</span>
              </>
            )}
            {focusLabel && (
              <>
                <span aria-hidden>/</span>
                <span className="font-mono text-foreground">{focusLabel}</span>
              </>
            )}
            {scoped && (
              <Button
                variant="ghost"
                size="xs"
                onClick={() => setScope({})}
                className="ml-1 text-muted-foreground"
              >
                Clear
              </Button>
            )}
          </div>

          <Tabs defaultValue="relationships" className="gap-4">
            <TabsList>
              <TabsTrigger value="relationships">Relationships</TabsTrigger>
              <TabsTrigger value="graph">Semantic graph</TabsTrigger>
              <TabsTrigger value="tables">Tables</TabsTrigger>
            </TabsList>

            <TabsContent value="relationships">
              <ErDiagram scope={scope} onScopeChange={setScope} onSelect={handleSelect} />
            </TabsContent>

            <TabsContent value="graph">
              <KnowledgeGraph scope={scope} onScopeChange={setScope} onSelect={handleSelect} />
            </TabsContent>

            <TabsContent value="tables">
              <TableBrowser
                scope={scope}
                onSelect={(id) => setSelection({ id, kind: "table", label: id })}
              />
            </TabsContent>
          </Tabs>
        </div>
      </div>

      <NodeDetailSheet
        selection={selection}
        open={selection !== null}
        onOpenChange={(o) => {
          if (!o) setSelection(null);
        }}
      />
    </PageShell>
  );
}

/** One rail entry: a namespace (with rollup badges) or the "All schemas" reset. */
function RailButton({
  label,
  rollup,
  active,
  onClick,
}: {
  label: string;
  rollup?: NamespaceRollup;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      className={cn(
        "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        active ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
      )}
    >
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {rollup && (
        <span className="flex shrink-0 items-center gap-1">
          {rollup.n_suspect > 0 && (
            <Badge
              variant="outline"
              className="border-tier-lineage/50 text-tier-lineage"
              title={`${rollup.n_suspect} suspect column${rollup.n_suspect === 1 ? "" : "s"}`}
            >
              {rollup.n_suspect}
            </Badge>
          )}
          {rollup.n_excluded > 0 && (
            <Badge
              variant="outline"
              className="border-tier-refused/40 text-tier-refused"
              title={`${rollup.n_excluded} excluded table${rollup.n_excluded === 1 ? "" : "s"}`}
            >
              {rollup.n_excluded}
            </Badge>
          )}
          <span
            className="text-xs text-muted-foreground"
            title={`${rollup.n_tables} table${rollup.n_tables === 1 ? "" : "s"}`}
          >
            {rollup.n_tables}
          </span>
        </span>
      )}
    </button>
  );
}
