"use client";

/**
 * The left pane of `/review`: clusters of observations, oldest first.
 *
 * **Oldest-first on the cluster's oldest member, and deliberately not by size.** A three-row
 * cluster from this morning is not more urgent than one row that has waited a month, and sorting by
 * size makes the long tail permanently invisible. That is also why the count of *distinct questions*
 * is on the row: it says whether this is one person hitting a wall repeatedly or several questions
 * blocked by one gap, and `n` alone cannot tell them apart.
 *
 * **The caption is not decoration.** The grouping is `(category, schema)` and nothing more — no
 * embedding, no model, no cost — so a steward who believes the machine decided two questions mean
 * the same thing will treat one cluster as one problem without checking. The caption says it did
 * not, permanently, and the measured weakness is under it.
 *
 * **Deliberately not shown here: SQL, the ledger, the record.** All of it is one click away in the
 * detail pane. A queue that shows the evidence is a queue nobody scans.
 */

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { QueryState } from "@/components/common/query-state";
import { useObservationClusters } from "@/hooks/queries";
import { CATEGORY_COPY, REVIEW_COPY } from "@/lib/review-copy";
import type { ObservationClusters } from "@/lib/types";

/** `2026-08-23T12:00:00Z` → something a person reads, or the raw value if it will not parse. */
function when(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleString();
}

export function ReviewQueue({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect: (key: string | null) => void;
}): React.JSX.Element {
  const query = useObservationClusters();

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="space-y-1">
        <p className="text-xs text-muted-foreground">{REVIEW_COPY.clusterCaption}</p>
        <p className="text-xs text-muted-foreground">{REVIEW_COPY.clusterWeakness}</p>
      </div>

      <QueryState
        query={query}
        isEmpty={(data: ObservationClusters) => data.clusters.length === 0}
        emptyMessage={REVIEW_COPY.storeEmpty}
      >
        {(data: ObservationClusters) => (
          // Not a flex column. `flex-col` here made every Card a flex item of a
          // height-constrained parent, and `flex-shrink` defaults to 1 -- so with more clusters
          // than fit, the browser compressed 54 cards to about 37px each and their content
          // overflowed, which reads as overlapping rows with the text clipped top and bottom.
          // `min-h-0 flex-1 space-y-2 overflow-y-auto pr-1` is the shape
          // `audit/audit-surface.tsx` already uses for the same job: the scroll box is the flex
          // child, the list inside it is not, so nothing can be squashed below its content.
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
            {data.meta.truncated && (
              <p className="text-xs text-destructive">
                This list is short: the server had more than it returned. Narrow it or page.
              </p>
            )}
            {data.clusters.map((cluster) => {
              const isSelected = cluster.key === selected;
              return (
                <Card
                  key={cluster.key}
                  role="button"
                  tabIndex={0}
                  aria-pressed={isSelected}
                  onClick={() => onSelect(isSelected ? null : cluster.key)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelect(isSelected ? null : cluster.key);
                    }
                  }}
                  className={`cursor-pointer p-3 text-sm transition-colors ${
                    isSelected ? "border-primary bg-accent/40" : "hover:bg-accent/20"
                  }`}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">{cluster.n}</Badge>
                    <span className="font-medium">
                      {cluster.category ? CATEGORY_COPY[cluster.category] ?? cluster.category : "Uncategorised"}
                    </span>
                    <code className="text-xs text-muted-foreground">{cluster.schema}</code>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {cluster.n_distinct_questions} distinct question
                    {cluster.n_distinct_questions === 1 ? "" : "s"} · oldest{" "}
                    {when(cluster.oldest_filed_at)}
                  </p>
                  {cluster.shared_missing_tables.length > 0 && (
                    // `break-words` on the code: `schema.table` gives the browser no break
                    // opportunity, so one long id (`world_development_indicators.jiao_zhu`)
                    // pushed the card 42px past its column — measured, at 1280px.
                    <p className="mt-1 text-xs">
                      Every member is missing:{" "}
                      <code className="break-words">
                        {cluster.shared_missing_tables.join(", ")}
                      </code>
                    </p>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </QueryState>
    </div>
  );
}
