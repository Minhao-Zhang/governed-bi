"use client";

/**
 * `/history` — every conversation on the server, as a page.
 *
 * **This was a popover on the chat page and it did not work.** The trigger sat above the
 * transcript and the panel opened downward over it: seventeen rows rendered from y=170 to
 * y=1240, past the answer card, past the composer, off the bottom of the viewport. A switcher
 * that covers the thing you are switching away from is not a switcher, and the list is not a
 * menu-sized object — it grows without bound as the server serves.
 *
 * So it is a route. Opening one navigates to `/?thread=<id>`, which is the same address the
 * chat page writes when it mints a thread, so there is exactly one way to say "this
 * conversation" and the back button works between the two pages for free.
 */

import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { MessagesSquare, Plus, Trash2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { useConversations } from "@/hooks/queries";
import { deleteConversation, type ConversationSummary } from "@/lib/threads";
import { QueryState } from "@/components/common/query-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

export function ConversationList() {
  const client = useQueryClient();
  const remove = useMutation({
    mutationFn: deleteConversation,
    onSuccess: () => void client.invalidateQueries({ queryKey: ["conversations"] }),
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Conversations are kept by the engine, not by this browser — they survive a reload and a
          server restart.
        </p>
        <Button asChild size="sm" variant="outline">
          <Link href="/">
            <Plus className="size-4" />
            New conversation
          </Link>
        </Button>
      </div>

      <QueryState
        query={useConversations(200)}
        isEmpty={(rows: ConversationSummary[]) => rows.length === 0}
        emptyMessage="No conversations yet. Ask something on the Chat page."
        skeleton={
          <div className="flex flex-col gap-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        }
      >
        {(rows) => (
          <ul className="flex flex-col divide-y rounded-md border">
            {rows.map((row) => (
              <Row
                key={row.thread_id}
                row={row}
                onDelete={() => remove.mutate(row.thread_id)}
                deleting={remove.isPending && remove.variables === row.thread_id}
              />
            ))}
          </ul>
        )}
      </QueryState>
    </div>
  );
}

function Row({
  row,
  onDelete,
  deleting,
}: {
  row: ConversationSummary;
  onDelete: () => void;
  deleting: boolean;
}) {
  return (
    <li className={cn("group flex items-center gap-3 px-3 py-2.5", deleting && "opacity-50")}>
      <MessagesSquare className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      {/* The whole row is the link, so the click target is the row and not a word in it. */}
      <Link href={`/?thread=${row.thread_id}`} className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-sm">
          {row.question ?? <span className="text-muted-foreground">Nothing asked yet</span>}
        </span>
        <span className="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
          {row.turns !== null && (
            <span>
              {row.turns} turn{row.turns === 1 ? "" : "s"}
            </span>
          )}
          <span>{when(row.updated_at)}</span>
          {/* Only when it is not `idle`. A badge on every row is noise; a badge on the one
              thread that is mid-run, or paused at a clarification, is the reason to look. */}
          {row.status !== "idle" && (
            <Badge variant="outline" className="font-mono text-[10px]">
              {row.status}
            </Badge>
          )}
        </span>
      </Link>
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={onDelete}
        disabled={deleting}
        aria-label="Delete this conversation"
        className="shrink-0 opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100"
      >
        <Trash2 className="size-3.5" />
      </Button>
    </li>
  );
}

/** A compact "how long ago", falling back to the raw timestamp rather than to a guess. */
function when(iso: string): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return iso;
  const seconds = Math.round((Date.now() - at) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
