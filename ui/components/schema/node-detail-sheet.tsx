"use client";

/**
 * Right-hand detail panel for a selected graph node. Fully controlled by the
 * parent (open + onOpenChange). For a table selection we resolve the full
 * TableView LAZILY (via useTableDetail) and render its columns with governance
 * flags; for every other kind we show the node's label / kind / provenance /
 * confidence carried on the selection itself.
 */

import { useState, type ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";
import { ColumnRelatedPanel } from "@/components/schema/column-related";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useTableDetail } from "@/hooks/queries";
import type { ColumnView, GraphNode, GraphSelection, TableView } from "@/lib/types";

function KindBadge({ kind }: { kind: string }) {
  return (
    <Badge variant="outline" className="font-mono uppercase">
      {kind}
    </Badge>
  );
}

/** Small label/value row used in the non-table view. */
function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="grid grid-cols-[7rem_1fr] gap-2 border-b pb-2 text-sm">
      <dt className="font-mono text-xs text-muted-foreground">{label}</dt>
      <dd className="break-words text-sm">{value}</dd>
    </div>
  );
}

function ColumnRow({
  column,
  onSelect,
}: {
  column: ColumnView;
  onSelect: () => void;
}) {
  const suspect = column.reliability === "suspect";
  return (
    <TableRow
      className={cn("cursor-pointer hover:bg-muted/50", column.excluded && "opacity-60")}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <TableCell className="font-mono text-xs">
        <span className="flex items-center gap-1">
          {/* right chevron = drill into this column's semantic-layer links */}
          <ChevronRight className="size-3 shrink-0 text-muted-foreground" aria-hidden />
          {column.physical_name}
        </span>
      </TableCell>
      <TableCell className="font-mono text-xs text-muted-foreground">
        {column.logical_type || column.physical_type}
      </TableCell>
      <TableCell className="text-xs">{column.role ?? "—"}</TableCell>
      <TableCell>
        <Badge
          variant="outline"
          className={cn(
            "font-normal",
            suspect
              ? "border-tier-lineage/50 text-tier-lineage"
              : "border-tier-governed/40 text-tier-governed",
          )}
          title={column.reliability_note ?? undefined}
        >
          {suspect ? "suspect" : "ok"}
        </Badge>
      </TableCell>
      <TableCell className="font-mono text-xs text-muted-foreground">
        {column.references ?? "—"}
      </TableCell>
      <TableCell>
        {column.excluded ? (
          <Badge
            variant="outline"
            className="border-tier-refused/40 text-tier-refused"
            title={column.excluded_reason ?? undefined}
          >
            excluded
          </Badge>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </TableCell>
    </TableRow>
  );
}

function TableDetail({ table }: { table: TableView }) {
  // Which column is drilled into (by physical name); null = the columns table.
  const [openColumn, setOpenColumn] = useState<string | null>(null);

  // The engine's own id for the drilled-into column, READ from the payload rather than
  // derived. The client used to compute `col_<table>_<physical>` (v1's scheme) while the
  // engine mints `{table_id}.{slug(physical_name)}` under ADR 0008 D1 — so every id this
  // panel asked for was one the engine had never heard of. `slug` hashes any name needing
  // sanitisation, which is why the fix is to stop deriving rather than to re-derive.
  const openColumnId =
    openColumn === null
      ? null
      : (table.columns.find((c) => c.physical_name === openColumn)?.id ?? null);

  // Drill-down: a focused column view with a breadcrumb back to the table. Gives
  // the semantic-layer links the full sheet height instead of cramping them below
  // a long columns table.
  if (openColumn) {
    return (
      <div className="space-y-4 px-4 pb-8">
        <button
          type="button"
          onClick={() => setOpenColumn(null)}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft className="size-4 shrink-0" aria-hidden />
          <span className="font-mono text-foreground">{table.physical_name}</span>
          <span aria-hidden>›</span>
          <span className="font-mono text-foreground">{openColumn}</span>
        </button>
        <div className="text-xs font-medium text-muted-foreground">Semantic-layer links</div>
        <ColumnRelatedPanel columnId={openColumnId} physicalName={openColumn} bare />
      </div>
    );
  }

  return (
    <div className="space-y-4 px-4 pb-8">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className="font-mono">
          {table.schema}
        </Badge>
        {table.excluded && (
          <Badge
            variant="outline"
            className="border-tier-refused/40 text-tier-refused"
            title={table.excluded_reason ?? undefined}
          >
            excluded
          </Badge>
        )}
        {table.provenance_status && (
          <Badge variant="outline" className="text-muted-foreground">
            {table.provenance_status}
          </Badge>
        )}
      </div>

      <dl className="grid gap-2">
        <Field
          label="rows"
          value={table.row_count !== null ? table.row_count.toLocaleString() : "—"}
        />
        <Field label="grain" value={table.grain ?? "—"} />
        {table.description && <Field label="description" value={table.description} />}
      </dl>

      <div>
        <div className="mb-1 text-xs font-medium text-muted-foreground">
          Columns ({table.columns.length}) —{" "}
          <span className="font-normal">select one to see its semantic-layer links</span>
        </div>
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Reliability</TableHead>
                <TableHead>References</TableHead>
                <TableHead>Excluded</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {table.columns.map((col) => (
                <ColumnRow
                  key={col.physical_name}
                  column={col}
                  onSelect={() => setOpenColumn(col.physical_name)}
                />
              ))}
            </TableBody>
          </Table>
        </div>
      </div>
    </div>
  );
}

function GenericDetail({ node }: { node: GraphNode }) {
  return (
    <div className="space-y-4 px-4 pb-8">
      <div className="flex flex-wrap items-center gap-2">
        {node.excluded && (
          <Badge variant="outline" className="border-tier-refused/40 text-tier-refused">
            excluded
          </Badge>
        )}
        {node.has_suspect && (
          <Badge variant="outline" className="border-tier-lineage/50 text-tier-lineage">
            suspect
          </Badge>
        )}
      </div>

      <Separator />

      <dl className="grid gap-2">
        <Field label="id" value={<span className="font-mono text-xs">{node.id}</span>} />
        <Field label="kind" value={node.kind} />
        <Field label="provenance" value={node.provenance_status ?? "—"} />
        <Field
          label="confidence"
          value={
            node.confidence !== null && node.confidence !== undefined
              ? node.confidence.toFixed(2)
              : "—"
          }
        />
      </dl>
    </div>
  );
}

/** Minimal detail for a non-table selection whose full node payload is absent. */
function MinimalDetail({ selection }: { selection: GraphSelection }) {
  return (
    <div className="space-y-4 px-4 pb-8">
      <Separator />
      <dl className="grid gap-2">
        <Field
          label="id"
          value={<span className="font-mono text-xs">{selection.id}</span>}
        />
        <Field label="kind" value={selection.kind} />
        <Field label="label" value={selection.label} />
      </dl>
    </div>
  );
}

/** Loading placeholder for the lazily-resolved columns area of a table. */
function TableDetailSkeleton() {
  return (
    <div className="space-y-4 px-4 pb-8">
      <div className="flex flex-wrap items-center gap-2">
        <Skeleton className="h-5 w-24" />
      </div>
      <div className="grid gap-2">
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-full" />
      </div>
      <div>
        <Skeleton className="mb-1 h-4 w-24" />
        <Skeleton className="h-40 w-full" />
      </div>
    </div>
  );
}

export function NodeDetailSheet({
  selection,
  open,
  onOpenChange,
}: {
  selection: GraphSelection | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const isTable = selection?.kind === "table";
  // Call unconditionally to obey the rules of hooks; null disables the query.
  const { data: table, isLoading: tableLoading } = useTableDetail(
    isTable ? selection.id : null,
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        {selection ? (
          <>
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2">
                <span className="truncate">{selection.label}</span>
                <KindBadge kind={selection.kind} />
              </SheetTitle>
              <SheetDescription>
                {isTable
                  ? "Table columns and their governance flags."
                  : "Semantic-layer asset detail."}
              </SheetDescription>
            </SheetHeader>

            {isTable ? (
              tableLoading ? (
                <TableDetailSkeleton />
              ) : table ? (
                <TableDetail table={table} />
              ) : (
                // Query errored or returned nothing — fall back gracefully.
                <div className="px-4 pb-8 text-sm text-muted-foreground">
                  No column detail is available for this table.
                </div>
              )
            ) : selection.node ? (
              <GenericDetail node={selection.node} />
            ) : (
              <MinimalDetail selection={selection} />
            )}
          </>
        ) : (
          <>
            <SheetHeader>
              <SheetTitle>No selection</SheetTitle>
              <SheetDescription>Pick a node in the graph to inspect it.</SheetDescription>
            </SheetHeader>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
