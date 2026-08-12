"use client";

/**
 * Plain, auditable table browser: an expandable, virtualized list of tables,
 * each revealing a columns table with governance flags. No accordion primitive
 * ships in the UI kit, so we roll a tiny controlled one (a Set of open ids that
 * survives virtualization recycling).
 *
 * Self-fetching, and no longer capability-gated: rows come from the lean
 * `/schema/summary` and a table's full columns hydrate lazily on expand via
 * `/schema/{id}`. The `can_scope: false` fallback read the flat `/schema` dump with every
 * column embedded; that route is deleted, because a catalog whose rows each carry a whole
 * table is not a lean catalog, and `can_scope` describes whether scope *parameters* are
 * honoured — not whether this page can render.
 */

import { useMemo, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useSchemaSummary, useTableDetail } from "@/hooks/queries";
import type {
  ColumnView,
  SchemaScope,
  TableSummary,
  TableView,
} from "@/lib/types";

/** Normalized collapsed-row shape, projected from a lean `TableSummary`. It used to be
 * projectable from a full `TableView` too, and carried the whole table in a `full` field for
 * the fallback path; both are gone with `GET /schema`. */
interface BrowserRow {
  id: string;
  physical_name: string;
  namespace: string;
  row_count: number | null;
  grain: string | null;
  excluded: boolean;
  excluded_reason: string | null;
  provenance_status: string | null;
  suspectCount: number;
  excludedCount: number;
  hasSuspect: boolean;
}

function summaryToRow(t: TableSummary): BrowserRow {
  const suspectCount = t.columns.filter((c) => c.reliability === "suspect").length;
  const excludedCount = t.columns.filter((c) => c.excluded).length;
  return {
    id: t.id,
    physical_name: t.physical_name,
    namespace: t.schema,
    row_count: t.row_count,
    grain: null,
    excluded: t.excluded,
    excluded_reason: null,
    provenance_status: t.provenance_status,
    suspectCount,
    excludedCount,
    hasSuspect: t.has_suspect,
  };
}


function ColumnRow({ column }: { column: ColumnView }) {
  const suspect = column.reliability === "suspect";
  return (
    <TableRow className={cn(column.excluded && "opacity-60")}>
      <TableCell className="font-mono text-xs">{column.physical_name}</TableCell>
      <TableCell className="font-mono text-xs text-muted-foreground">
        {column.logical_type || column.physical_type}
        {!column.nullable && <span className="ml-1 text-[0.65rem]" title="not null">·NN</span>}
        {column.is_unique && <span className="ml-1 text-[0.65rem]" title="unique">·U</span>}
      </TableCell>
      <TableCell className="text-xs">{column.role ?? "—"}</TableCell>
      <TableCell className="font-mono text-xs text-muted-foreground">
        {column.references ?? "—"}
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap gap-1">
          {suspect && (
            <Badge
              variant="outline"
              className="border-tier-lineage/50 text-tier-lineage"
              title={column.reliability_note ?? undefined}
            >
              suspect
            </Badge>
          )}
          {column.excluded && (
            <Badge
              variant="outline"
              className="border-tier-refused/40 text-tier-refused"
              title={column.excluded_reason ?? undefined}
            >
              excluded
            </Badge>
          )}
          {!suspect && !column.excluded && (
            <span className="text-xs text-muted-foreground">—</span>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

/** The columns table shown on expand, rendered from a fully-hydrated TableView
 * (columns present in both scoped `/schema/{id}` and fallback `/schema`). */
function ColumnsTable({ table }: { table: TableView }) {
  return (
    <div className="border-t">
      {table.description && (
        <p className="px-3 py-2 text-xs text-muted-foreground">{table.description}</p>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Column</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>References</TableHead>
            <TableHead>Governance</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {table.columns.map((col) => (
            <ColumnRow key={col.physical_name} column={col} />
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** Scoped mode: hydrate a table's full columns lazily when the row opens. */
function LazyColumns({ id }: { id: string }) {
  const { data, isLoading, isError } = useTableDetail(id);

  if (isLoading) {
    return (
      <div className="space-y-2 border-t px-3 py-3">
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="border-t px-3 py-2 text-xs text-muted-foreground">
        Couldn&apos;t load columns for this table.
      </div>
    );
  }
  return <ColumnsTable table={data} />;
}

/** Collapsed-row header + expand affordance. The name opens the detail sheet
 * (onSelect); the chevron and the metadata line toggle the inline expand. */
function RowHeader({
  row,
  open,
  onToggle,
  onSelect,
}: {
  row: BrowserRow;
  open: boolean;
  onToggle: () => void;
  onSelect?: (id: string) => void;
}) {
  const hasMeta = row.row_count !== null || !!row.grain;
  return (
    <div className="flex w-full items-center gap-2 px-3 py-2.5">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-label={open ? "Collapse table" : "Expand table"}
        className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      >
        <ChevronRight
          className={cn("size-4 transition-transform", open && "rotate-90")}
          aria-hidden
        />
      </button>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onSelect?.(row.id)}
            title="Open details"
            className="truncate rounded text-left font-mono text-sm font-medium hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            {row.physical_name}
          </button>
          <span className="shrink-0 text-xs text-muted-foreground">{row.namespace}</span>
        </div>
        <button
          type="button"
          onClick={onToggle}
          className="mt-0.5 block max-w-full truncate text-left text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          {hasMeta ? (
            <>
              {row.row_count !== null && <span>{row.row_count.toLocaleString()} rows</span>}
              {row.row_count !== null && row.grain && <span> · </span>}
              {row.grain && <span>grain: {row.grain}</span>}
            </>
          ) : (
            <span>{open ? "hide columns" : "show columns"}</span>
          )}
        </button>
      </div>

      <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
        {(row.suspectCount > 0 || row.hasSuspect) && (
          <Badge variant="outline" className="border-tier-lineage/50 text-tier-lineage">
            {row.suspectCount > 0 ? `${row.suspectCount} suspect` : "suspect"}
          </Badge>
        )}
        {row.excludedCount > 0 && (
          <Badge variant="outline" className="border-tier-refused/40 text-tier-refused">
            {row.excludedCount} excluded col
          </Badge>
        )}
        {row.excluded && (
          <Badge
            variant="outline"
            className="border-tier-refused/40 text-tier-refused"
            title={row.excluded_reason ?? undefined}
          >
            excluded
          </Badge>
        )}
        {row.provenance_status && (
          <Badge variant="outline" className="text-muted-foreground">
            {row.provenance_status}
          </Badge>
        )}
      </div>
    </div>
  );
}

export function TableBrowser({
  scope,
  onSelect,
}: {
  scope?: SchemaScope;
  onSelect?: (id: string) => void;
}) {
  // The lean summary is the only catalog now: columns come with the row and prose is
  // hydrated lazily on expand. The `can_scope: false` branch read the flat `/schema` dump,
  // which is deleted — it was a second projection of these same tables.
  const summary = useSchemaSummary(scope);

  const rows = useMemo<BrowserRow[]>(() => {
    let items = summary.data?.items ?? [];
    if (scope?.schema) items = items.filter((t) => t.schema === scope.schema);
    return items.map(summaryToRow);
  }, [summary.data, scope?.schema]);

  const isLoading = summary.isLoading;

  // Open rows tracked by id (survives virtualization recycling).
  const [openIds, setOpenIds] = useState<Set<string>>(() => new Set());
  const toggle = (id: string) =>
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const scrollRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 58,
    overscan: 6,
  });

  if (isLoading && rows.length === 0) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-[52px] w-full rounded-md" />
        ))}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="rounded-md border px-3 py-8 text-center text-sm text-muted-foreground">
        No tables yet.
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="max-h-[70vh] overflow-auto">
      <div
        className="relative w-full"
        style={{ height: `${rowVirtualizer.getTotalSize()}px` }}
      >
        {rowVirtualizer.getVirtualItems().map((virtualRow) => {
          const row = rows[virtualRow.index];
          const open = openIds.has(row.id);
          return (
            <div
              key={row.id}
              data-index={virtualRow.index}
              ref={rowVirtualizer.measureElement}
              className="absolute left-0 top-0 w-full pb-2"
              style={{ transform: `translateY(${virtualRow.start}px)` }}
            >
              <div className="rounded-md border">
                <RowHeader
                  row={row}
                  open={open}
                  onToggle={() => toggle(row.id)}
                  onSelect={onSelect}
                />
                {/* Columns hydrate lazily from GET /schema/{id}. The other branch read them
                    inline off the flat /schema dump, which is deleted — a row that carries a
                    whole table is a catalog that is not lean. */}
                {open && <LazyColumns id={row.id} />}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
