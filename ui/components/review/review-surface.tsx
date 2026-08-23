"use client";

/**
 * The two-pane shell for `/review`.
 *
 * **`?cluster=` in the URL, not `useState`.** A steward's whole job on this screen is handing a
 * decision to somebody else, and "look at this" has to be a link. `/audit` gets away with local
 * state because nobody links to a trace.
 *
 * **The panes scroll internally rather than growing the page.** Copied structurally from
 * `AuditSurface`, which already solved the same thing: selecting a row must not make the page taller,
 * because the decision belongs on screen with the evidence. `/review` has no decision bar yet, and
 * when it gains one this is why it can be sticky.
 */

import { ClusterPanel } from "@/components/review/cluster-panel";
import { ReviewQueue } from "@/components/review/review-queue";
import { useQueryParam } from "@/hooks/use-query-param";
import { REVIEW_COPY } from "@/lib/review-copy";

export function ReviewSurface(): React.JSX.Element {
  const { value: cluster, setValue: setCluster } = useQueryParam("cluster");

  return (
    <div className="grid min-h-0 flex-1 gap-6 lg:grid-cols-[minmax(20rem,26rem)_1fr]">
      <div className="flex min-h-0 flex-col">
        <ReviewQueue selected={cluster} onSelect={setCluster} />
      </div>
      <div className="flex min-h-0 flex-col">
        {cluster ? (
          <ClusterPanel clusterKey={cluster} />
        ) : (
          <p className="text-sm text-muted-foreground">{REVIEW_COPY.selectPrompt}</p>
        )}
      </div>
    </div>
  );
}
