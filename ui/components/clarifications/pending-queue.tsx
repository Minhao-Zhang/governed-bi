"use client";

/**
 * Questions the engine asked that nobody answered — every conversation, oldest first.
 *
 * **Why this surface exists at all.** When the engine is unsure which of two readings a question
 * means, it stops and asks. If the reader answers, the answer is recorded and the turn finishes. If
 * they close the tab, *nothing is recorded* — the engine writes a clarification only on the far side
 * of `interrupt()` — so the question sits paused forever and no log anywhere mentions it. That is
 * the half `/audit` structurally cannot show, and it is the half where someone is waiting.
 *
 * **Oldest first**, unlike `/audit`. A log is read newest-first because the newest event is the one
 * you came for; a queue is read oldest-first because the row that has waited longest is the one to
 * act on.
 *
 * **Read-only, and the empty state says so.** There is no answer button. Answering would mean
 * resuming a thread this operator was not the one asked, which the engine refuses by design
 * (ADR 0006 B9); the owner's decision routes an operator's answer into the semantic layer instead,
 * and that path waits on a provenance gate the engine does not have yet. Until then this shows the
 * backlog rather than pretending to clear it — a button that silently did nothing would be worse
 * than no button.
 */

import { Clock, MessageCircleQuestion } from "lucide-react";

import { QueryState } from "@/components/common/query-state";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { usePendingClarifications } from "@/hooks/queries";
import { atLeast, useDisplayMode } from "@/lib/display-mode";
import type { PendingQueue } from "@/lib/types";

/** `2026-08-19T10:00:00Z` → something a person reads, or the raw value if it will not parse. */
function when(iso: string | null): string {
  if (!iso) return "unknown";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleString();
}

export function PendingQueueSurface() {
  const query = usePendingClarifications();
  const mode = useDisplayMode();

  return (
    <QueryState
      query={query}
      isEmpty={(data: PendingQueue) => data.rows.length === 0}
      emptyMessage="No unanswered questions. Every clarification the engine asked has been answered or abandoned with the conversation."
    >
      {(data: PendingQueue) => (
        <div className="space-y-3">
          {/* The count a caller did not get, beside the ones it did. A silently short queue reads
              as "nobody is waiting", and what is being under-reported is a person. */}
          {data.meta.truncated && (
            <p className="text-tier-fenced-raw text-sm">
              Showing the {data.meta.n} oldest. There are more waiting than this
              — the list is cut off, not complete.
            </p>
          )}

          {data.rows.map((row) => (
            <Card
              key={
                row.clarification_id ??
                row.report_id ??
                `${row.thread_id}-${row.asked_at}`
              }
              className="p-4"
            >
              <div className="flex gap-3">
                <MessageCircleQuestion className="text-tier-lineage mt-0.5 size-4 shrink-0" />
                <div className="min-w-0 flex-1 space-y-2">
                  <p className="text-sm font-medium">
                    {row.question ?? "(no question recorded)"}
                  </p>
                  {row.why && (
                    <p className="text-muted-foreground text-sm">{row.why}</p>
                  )}
                  <div className="text-muted-foreground flex flex-wrap items-center gap-3 text-xs">
                    <span className="inline-flex items-center gap-1">
                      <Clock className="size-3.5" /> asked {when(row.asked_at)}
                    </span>
                    {/* The ids are an engineer's handle on the row -- which thread to open, which
                        turn asked. A business reader has nothing to do with either. */}
                    {atLeast(mode, "engineer") && row.thread_id && (
                      <Badge variant="outline" className="font-mono text-xs">
                        thread {row.thread_id.slice(0, 8)}
                      </Badge>
                    )}
                    {atLeast(mode, "engineer") && row.turn_id && (
                      <Badge variant="outline" className="font-mono text-xs">
                        turn {row.turn_id.slice(0, 8)}
                      </Badge>
                    )}
                    {row.source && (
                      <Badge variant="outline" className="text-xs">
                        {row.source === "interrupt"
                          ? "clarification"
                          : row.source === "from_refusal"
                            ? "flagged refusal"
                            : "flagged answer"}
                      </Badge>
                    )}
                  </div>
                </div>
              </div>
            </Card>
          ))}

          {/* Distinguishes "nothing is waiting" from "the store was not read", which otherwise
              look identical -- the reason the route reports it.

              Not "paused conversations" any more: the reader walks the paused threads *and*
              then the whole store, because an open note lives on a thread of any status. The
              number is distinct threads across both walks. */}
          {atLeast(mode, "analyst") && (
            <p className="text-muted-foreground text-xs">
              {data.meta.threads_scanned} conversation
              {data.meta.threads_scanned === 1 ? "" : "s"} read.
            </p>
          )}
        </div>
      )}
    </QueryState>
  );
}
