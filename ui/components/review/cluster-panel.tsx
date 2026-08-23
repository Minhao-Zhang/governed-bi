"use client";

/**
 * The right pane: one cluster's members, and the evidence for whichever one is open.
 *
 * **The leader's evidence in full, the rest as a strip.** A cluster's members share a category and a
 * schema and nothing else — measured, their missing tables are mostly *different* tables — so
 * reading one in full and the others as one line each is the honest shape. A panel that showed all
 * of them in full would be asserting they are the same problem, which is exactly what the queue's
 * caption says nobody checked.
 *
 * Read-only. There is no decision bar yet: drafting a change needs `corpus/patch.py` and the diff
 * renderer, and a button that produced nothing would be worse than its absence.
 */

import { useState } from "react";

import { EvidenceBundle } from "@/components/review/evidence-bundle";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { QueryState } from "@/components/common/query-state";
import { useObservationClusters } from "@/hooks/queries";
import { REVIEW_COPY } from "@/lib/review-copy";
import type { ObservationClusters } from "@/lib/types";

export function ClusterPanel({ clusterKey }: { clusterKey: string }): React.JSX.Element {
  const query = useObservationClusters();
  const [openId, setOpenId] = useState<string | null>(null);

  return (
    <QueryState
      query={query}
      isEmpty={(data: ObservationClusters) =>
        !data.clusters.some((cluster) => cluster.key === clusterKey)
      }
      emptyMessage="That cluster is no longer in the queue. It may have been triaged in another tab."
    >
      {(data: ObservationClusters) => {
        const cluster = data.clusters.find((c) => c.key === clusterKey);
        if (!cluster) return <></>;
        const open =
          cluster.observations.find((o) => o.observation_id === openId) ?? cluster.observations[0];

        return (
          <div className="flex min-h-0 flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2">
              <code className="text-xs text-muted-foreground">{cluster.key}</code>
              <Badge variant="outline">
                {cluster.n} observation{cluster.n === 1 ? "" : "s"}
              </Badge>
            </div>

            {cluster.observations.length > 1 && (
              <div className="flex flex-wrap gap-2">
                {cluster.observations.map((observation) => (
                  <button
                    key={observation.observation_id}
                    type="button"
                    onClick={() => setOpenId(observation.observation_id)}
                    className={`max-w-[22rem] truncate rounded-md border px-2 py-1 text-left text-xs ${
                      observation.observation_id === open.observation_id
                        ? "border-primary bg-accent/40"
                        : "hover:bg-accent/20"
                    }`}
                    title={observation.question}
                  >
                    {observation.question}
                  </button>
                ))}
              </div>
            )}

            <Card className="min-h-0 overflow-y-auto p-4">
              <EvidenceBundle observation={open} />
            </Card>

            <p className="text-xs text-muted-foreground">{REVIEW_COPY.clusterCaption}</p>
          </div>
        );
      }}
    </QueryState>
  );
}
