"use client";

/**
 * The server's recent conversations, shown on a new conversation until P13's sidebar replaces it.
 * Opening one writes `?thread=<id>`, the same address the chat page writes when it mints a thread.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { MessagesSquare, Trash2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { useConversations } from "@/hooks/queries";
import { deleteConversation, type ConversationSummary } from "@/lib/threads";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export function RecentConversations({ onOpen }: { onOpen: (threadId: string) => void }) {
  const client = useQueryClient();
  const { data: rows } = useConversations();
  const remove = useMutation({
    mutationFn: deleteConversation,
    onSuccess: () => void client.invalidateQueries({ queryKey: ["conversations"] }),
  });

  if (!rows || rows.length === 0) return null;

  return (
    <section className="flex w-full max-w-xl flex-col gap-2 text-left">
      <h2 className="text-xs text-muted-foreground">Recent conversations</h2>
      <ul className="max-h-80 divide-y overflow-y-auto rounded-md border">
        {rows.map((row) => (
          <Row
            key={row.thread_id}
            row={row}
            onOpen={() => onOpen(row.thread_id)}
            onDelete={() => remove.mutate(row.thread_id)}
            deleting={remove.isPending && remove.variables === row.thread_id}
          />
        ))}
      </ul>
    </section>
  );
}

function Row({
  row,
  onOpen,
  onDelete,
  deleting,
}: {
  row: ConversationSummary;
  onOpen: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  return (
    <li className={cn("group flex items-center gap-3 px-3 py-2", deleting && "opacity-50")}>
      <MessagesSquare className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      <button
        type="button"
        onClick={onOpen}
        className="flex min-w-0 flex-1 flex-col gap-0.5 text-left"
      >
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
          {/* Only when not idle: the thread mid-run or paused at a clarification is the one to notice. */}
          {row.status !== "idle" && (
            <Badge variant="outline" className="font-mono text-[10px]">
              {row.status}
            </Badge>
          )}
        </span>
      </button>
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
