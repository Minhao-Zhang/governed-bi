"use client";

/**
 * Admin drafts queue (task D, utku-ai-trust-loop-plan.md) -- every corpus asset still
 * `proposed`, waiting on `POST /corpus/drafts/{id}/approve`. The loop's missing terminus:
 * that route is the only function that ever flips `proposed` to `certified`, and until this
 * panel nothing in the UI called it -- every draft this project has certified so far went
 * through the route by hand.
 *
 * **Why this surface and not the other two candidates.** `AssumptionsLog` reads the same
 * `/corpus/assumptions` route this panel could have reused, but that route folds in both
 * `proposed` and `certified` clarification-derived terms with no status field at all -- by
 * design, it is a settled history, not a work queue, and it only ever sees clarification-
 * derived `TermAsset`s (`curation_routes.py::_is_clarification_derived`), not every producer of
 * a draft. `AssetBrowser`/`AssetTable` already show `proposed` rows via their provenance filter,
 * but an approve button dropped onto one of those rows would have no room to also show why the
 * row exists -- the browser's grid clamps every cell to keep ~4.2k rows scannable, which is
 * backwards for the one row type where more context, not less, is the point. A dedicated queue,
 * mirroring `ConflictsPanel`/`ClarificationsPanel`, is the one place built to show a full
 * question-and-answer per card and put the decision beside it.
 *
 * Reuses `/corpus/assets` (GET, already declared, already returns `provenance_status`) rather
 * than a new listing route -- narrowed here to `proposed` and rendered unclamped, since a
 * clarification-derived term's `summary` already **is** its question-answer pair
 * (`curator/clarification.py::_qa_summary`), truncated only at the 250-char budget the engine
 * itself writes to disk, not by this component's CSS.
 */

import { FileClock } from "lucide-react";

import type { AssetRow } from "@/lib/types";
import { useAssets } from "@/hooks/queries";
import { QueryState } from "@/components/common/query-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** A draft is any non-table asset still `proposed` -- the one status
 * `POST /corpus/drafts/{id}/approve` will accept (`corpus/drafts.py::DraftNotPending`). */
const isDraft = (row: AssetRow) => row.provenance_status === "proposed";

export function DraftsPanel() {
  const assets = useAssets();

  return (
    <QueryState
      query={assets}
      isEmpty={(data) => data.filter(isDraft).length === 0}
      emptyMessage="No drafts waiting on approval — every proposed asset has been certified or none exist yet."
    >
      {(data) => (
        <div className="space-y-3">
          {data.filter(isDraft).map((row) => (
            <DraftCard key={row.id} row={row} />
          ))}
        </div>
      )}
    </QueryState>
  );
}

function DraftCard({ row }: { row: AssetRow }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-2">
          <FileClock className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <CardTitle className="flex-1 text-sm leading-snug font-medium">{row.summary}</CardTitle>
          <div className="flex shrink-0 items-center gap-2">
            <Badge variant="outline" className="font-mono">
              {row.asset_type}
            </Badge>
            {row.schema && (
              <Badge variant="outline" className="font-mono text-muted-foreground">
                {row.schema}
              </Badge>
            )}
            <Badge variant="outline" className="text-muted-foreground">
              {row.id}
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-xs text-muted-foreground">
          Still proposed — the certified-only check in corpus/analyst.py keeps this from
          licensing a column in a live answer until an admin approves it.
        </p>
      </CardContent>
    </Card>
  );
}
