"use client";

/**
 * Admin "agreed assumptions" log (round 9). Every admin-answered clarification
 * that has been folded into the corpus as a governed `NoteAsset` (see
 * `AssetBag.record_caveats` / `GET /corpus/assumptions`), rendered as a plain
 * question→answer history — not an editable asset grid like `AssetBrowser`.
 * Read-only by design: editing an agreed assumption happens through the
 * Clarifications flow (re-answering), not here.
 */

import { CheckCircle2, ClipboardList, MessagesSquare } from "lucide-react";

import type { AssumptionRow } from "@/lib/types";
import { useAssumptions } from "@/hooks/queries";
import { QueryState } from "@/components/common/query-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";

export function AssumptionsLog() {
  const assumptions = useAssumptions();

  return (
    <QueryState
      query={assumptions}
      isEmpty={(data) => data.length === 0}
      emptyMessage="No agreed assumptions yet — answer a clarification to add one."
    >
      {(data) => (
        <div className="space-y-3">
          {data.map((row) => (
            <AssumptionCard key={row.id} row={row} />
          ))}
        </div>
      )}
    </QueryState>
  );
}

function AssumptionCard({ row }: { row: AssumptionRow }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-2">
          <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <p className="flex-1 text-sm leading-snug font-medium">{row.question}</p>
          <div className="flex shrink-0 items-center gap-2">
            <SourceBadge source={row.source} />
            <Badge variant="outline" className="text-muted-foreground">
              {row.id}
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-sm text-muted-foreground">{row.answer}</p>
        <p className="text-xs text-muted-foreground">
          {row.answered_by ? `Answered by ${row.answered_by}` : "Answered by unknown"}
          {row.answered_at ? ` · ${formatTimestamp(row.answered_at)}` : ""}
        </p>
      </CardContent>
    </Card>
  );
}

function SourceBadge({ source }: { source: AssumptionRow["source"] }) {
  if (source === "live_chat") {
    return (
      <Badge variant="secondary" className="text-muted-foreground">
        <MessagesSquare className="size-3" />
        From live chat
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-muted-foreground">
      <ClipboardList className="size-3" />
      From corpus review
    </Badge>
  );
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}
