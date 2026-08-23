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
 * **The open row is re-fetched by id rather than read out of the cluster.** The grouped projection
 * does not join patches -- every member arrives with `patches: []` -- so a decision bar built on the
 * cluster's copy would report "nothing drafted" about a row that already has a patch, and offer to
 * draft a second one.
 */

import { useState } from "react";

import { DecisionBar } from "@/components/review/decision-bar";
import { EvidenceBundle } from "@/components/review/evidence-bundle";
import { HandoffPanel } from "@/components/review/handoff-panel";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { QueryState } from "@/components/common/query-state";
import { useObservation, useObservationClusters } from "@/hooks/queries";
import { REVIEW_COPY } from "@/lib/review-copy";
import type { ObservationClusters } from "@/lib/types";

export function ClusterPanel({ clusterKey }: { clusterKey: string }): React.JSX.Element {
  const query = useObservationClusters();
  const [openId, setOpenId] = useState<string | null>(null);
  const [drafting, setDrafting] = useState(false);
  const detail = useObservation(openId);

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
        const listed =
          cluster.observations.find((o) => o.observation_id === openId) ?? cluster.observations[0];
        // The detail row when it has arrived for *this* observation, the listed one until then. A
        // stale detail from the previously selected row would draw another row's patches here.
        const open =
          detail.data && detail.data.observation_id === listed.observation_id
            ? detail.data
            : listed;

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
                    onClick={() => {
                      setOpenId(observation.observation_id);
                      setDrafting(false);
                    }}
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

            <HandoffPanel
              observation={open}
              open={drafting}
              onClose={() => setDrafting(false)}
            />

            <DecisionBar observation={open} onDraft={() => setDrafting(true)} />

            <p className="text-xs text-muted-foreground">{REVIEW_COPY.clusterCaption}</p>
          </div>
        );
      }}
    </QueryState>
  );
}
