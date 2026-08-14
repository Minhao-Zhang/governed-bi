"use client";

/**
 * "Click a column → every semantic-layer item that touches it" (handoff §14).
 * Fetches GET /columns/{column_id}/related lazily and renders terms, notes
 * (wire key `rules`), FK in/out, server-resolved joins, and table-grain metrics.
 * All links carry provenance / confidence so draft / low-confidence items flag
 * the same way they do elsewhere. Joins are pre-resolved server-side — we never
 * parse ON strings.
 */

import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useAssets, useColumnRelated } from "@/hooks/queries";
import type { ColumnRelated } from "@/lib/types";
import { cn } from "@/lib/utils";

export function ColumnRelatedPanel({
  columnId,
  physicalName,
  bare = false,
}: {
  /** The engine's column asset id, from the payload. `null` when the loaded table carries
   * no id for this column — the query stays disabled and the panel says so, which is the
   * truthful reading: the id is the engine's to mint, so its absence is a fact about the
   * payload rather than something to guess around. */
  columnId: string | null;
  physicalName: string;
  /** Drop the bordered box + "Related to X" header — the caller (e.g. a
   * drill-down breadcrumb) already provides that context. */
  bare?: boolean;
}) {
  const { data, isLoading, isError } = useColumnRelated(columnId);

  const content = isLoading ? (
    <div className="space-y-2">
      <Skeleton className="h-5 w-40" />
      <Skeleton className="h-5 w-56" />
      <Skeleton className="h-5 w-32" />
    </div>
  ) : isError ? (
    <p className="text-xs text-muted-foreground">
      Couldn&apos;t load related items for this column.
    </p>
  ) : !data || data.meta?.column_resolvable === false ? (
    <p className="text-xs text-muted-foreground">
      This column isn&apos;t resolvable in the corpus.
    </p>
  ) : (
    <RelatedBody data={data} />
  );

  if (bare) {
    return (
      <div className="space-y-3">
        <div className="break-all font-mono text-[10px] text-muted-foreground">{columnId}</div>
        {content}
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-md border bg-muted/30 p-3">
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-xs font-medium text-muted-foreground">
          Related to <span className="font-mono text-foreground">{physicalName}</span>
        </div>
        <span className="truncate font-mono text-[10px] text-muted-foreground">{columnId}</span>
      </div>
      {content}
    </div>
  );
}

/** Pull the aggregate expression out of an asset summary
 * ("count_x: COUNT(t.\"x\")" → "COUNT(t.\"x\")"); fall back to the whole string. */
function metricExpression(summary: string): string {
  const idx = summary.indexOf(": ");
  return idx >= 0 ? summary.slice(idx + 2) : summary;
}

function RelatedBody({ data }: { data: ColumnRelated }) {
  // Metric definitions aren't on the column endpoint (table-grain, §14.4), so
  // enrich each metric with its expression + provenance from the corpus assets.
  const { data: metricAssets } = useAssets("metric");
  const metricDefs = new Map((metricAssets ?? []).map((a) => [a.id, a]));

  const empty =
    data.terms.length === 0 &&
    data.rules.length === 0 &&
    data.fk_out === null &&
    data.fk_in.length === 0 &&
    data.joins.length === 0 &&
    data.metrics.length === 0;

  if (empty) {
    return (
      <p className="text-xs text-muted-foreground">
        No semantic-layer items reference this column.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <Section title="Terms" count={data.terms.length}>
        {data.terms.map((t) => (
          <Item key={t.id} id={t.id} confidence={t.confidence} provenance={t.provenance_status}>
            <span className="font-medium">{t.name}</span>
            {t.synonyms.length > 0 && (
              <span className="text-muted-foreground"> · {t.synonyms.join(", ")}</span>
            )}
          </Item>
        ))}
      </Section>

      <Section title="Notes" count={data.rules.length}>
        {data.rules.map((r) => (
          <Item key={r.id} id={r.id} confidence={r.confidence} provenance={r.provenance_status}>
            <span className="text-muted-foreground">[{r.kind}] </span>
            {r.statement}
          </Item>
        ))}
      </Section>

      <Section title="Foreign keys" count={(data.fk_out ? 1 : 0) + data.fk_in.length}>
        {data.fk_out && (
          <Item id={data.fk_out.column_id}>
            <span className="text-muted-foreground">references → </span>
            <span className="font-mono">
              {physicalOf(data.fk_out.table_id)}.{data.fk_out.physical_name}
            </span>
          </Item>
        )}
        {data.fk_in.map((f) => (
          <Item key={f.column_id} id={f.column_id}>
            <span className="text-muted-foreground">referenced by ← </span>
            <span className="font-mono">
              {physicalOf(f.table_id)}.{f.physical_name}
            </span>
          </Item>
        ))}
      </Section>

      <Section title="Joins" count={data.joins.length}>
        {data.joins.map((j) => (
          <Item
            key={j.id}
            id={j.id}
            confidence={j.confidence}
            warn={j.low_confidence}
          >
            <span className="font-mono text-xs">{j.on}</span>
            {j.cardinality && (
              <span className="text-muted-foreground"> · {j.cardinality}</span>
            )}
          </Item>
        ))}
      </Section>

      <Section title="Metrics" count={data.metrics.length}>
        {data.metrics.map((m) => {
          const def = metricDefs.get(m.id);
          const expr = def ? metricExpression(def.summary) : null;
          return (
            <Item key={m.id} id={m.id} provenance={def?.provenance_status}>
              <span className="font-medium">{m.name}</span>
              {/* The metric's definition, joined from /corpus/assets. */}
              {expr && (
                <span className="mt-0.5 block font-mono text-xs text-muted-foreground">{expr}</span>
              )}
              {/* §14.4: metric linkage is table-grain, never column-precise. */}
              <span className="text-[10px] text-muted-foreground">on this table</span>
            </Item>
          );
        })}
      </Section>
    </div>
  );
}

/** Strip the `tbl_` prefix for a compact physical-ish label in FK rows. */
function physicalOf(tableId: string): string {
  return tableId.startsWith("tbl_") ? tableId.slice(4) : tableId;
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: ReactNode;
}) {
  if (count === 0) return null;
  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {title}
        <span className="rounded-full bg-muted px-1.5 text-[10px] tabular-nums">{count}</span>
      </div>
      <ul className="space-y-1">{children}</ul>
    </div>
  );
}

function Item({
  id,
  children,
  confidence,
  provenance,
  warn = false,
}: {
  id: string;
  children: ReactNode;
  confidence?: number | null;
  provenance?: string | null;
  warn?: boolean;
}) {
  return (
    <li
      className={cn(
        "flex items-start justify-between gap-2 rounded border px-2 py-1 text-sm",
        warn ? "border-tier-fenced-raw/50 bg-tier-fenced-raw/5" : "border-transparent bg-background",
      )}
      title={id}
    >
      <span className="min-w-0 break-words">{children}</span>
      <span className="flex shrink-0 items-center gap-1">
        {provenance && provenance !== "certified" && (
          <Badge variant="outline" className="border-tier-lineage/50 px-1 py-0 text-[10px] text-tier-lineage">
            {provenance}
          </Badge>
        )}
        {typeof confidence === "number" && (
          <span className="font-mono text-[10px] text-muted-foreground">{confidence.toFixed(2)}</span>
        )}
      </span>
    </li>
  );
}
