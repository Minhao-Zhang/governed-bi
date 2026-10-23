"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { groupConversations } from "@/components/sidebar/groups";
import { useConversations } from "@/hooks/queries";
import { useThreadId } from "@/hooks/use-thread-id";
import { ENDING_WORDS } from "@/lib/endings";
import { USE_MOCKS } from "@/lib/env";
import type { ConversationRow } from "@/lib/types";

/** `now` pins the date groups. The page leaves it unset. */
export function Sidebar({ now }: { now?: Date }) {
  const { threadId, setThreadId } = useThreadId();
  const client = useQueryClient();
  const conversations = useConversations();
  const clock = now ?? new Date();

  // A thread change refreshes the list at once. A run that keeps the same
  // thread shows up on the next poll (hooks/queries.ts).
  const skippedMount = useRef(false);
  useEffect(() => {
    if (USE_MOCKS) return;
    if (!skippedMount.current) {
      skippedMount.current = true;
      return;
    }
    void client.invalidateQueries({ queryKey: ["conversations"] });
  }, [threadId, client]);

  const rows = (conversations.data?.rows ?? []).filter(
    (row): row is ConversationRow & { thread_id: string } => row.thread_id != null,
  );
  const waiting = rows.filter((row) => row.waiting);
  const groups = groupConversations(
    rows.filter((row) => !row.waiting),
    clock,
  );

  const item = (row: ConversationRow & { thread_id: string }) => (
    <li key={row.thread_id}>
      <ConversationButton
        row={row}
        open={threadId === row.thread_id}
        onOpen={() => setThreadId(row.thread_id)}
      />
    </li>
  );

  return (
    <aside className="flex h-full min-h-0 w-[260px] shrink-0 flex-col border-r bg-[#f6f6f6] px-2 py-3 text-[var(--text)]">
      <button
        type="button"
        onClick={() => setThreadId(null)}
        className="w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-white"
      >
        New conversation
      </button>
      <Link
        href="/conversations"
        className="mb-2 block w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-white"
      >
        Conversations
      </Link>
      <nav aria-label="Conversations" className="min-h-0 flex-1 overflow-y-auto">
        {waiting.length > 0 && (
          <section aria-labelledby="sidebar-waiting">
            <h2 id="sidebar-waiting" className="px-2 pb-1 text-xs text-[var(--muted)]">
              Waiting for you
            </h2>
            <ul className="flex flex-col gap-0.5">{waiting.map(item)}</ul>
          </section>
        )}
        {groups.map((group) => {
          const id = `sidebar-${group.title.replaceAll(" ", "-").toLowerCase()}`;
          return (
            <section key={group.title} aria-labelledby={id}>
              <h2 id={id} className="px-2 pt-4 pb-1 text-xs text-[var(--muted)]">
                {group.title}
              </h2>
              <ul className="flex flex-col gap-0.5">
                {group.rows.map(item)}
              </ul>
            </section>
          );
        })}
      </nav>
    </aside>
  );
}

export function labelFor(question: string | null | undefined): string {
  const text = question?.trim() ?? "";
  return text.length > 0 ? text : "Nothing asked yet";
}

function ConversationButton({
  row,
  open,
  onOpen,
}: {
  row: ConversationRow;
  open: boolean;
  onOpen: () => void;
}) {
  const label = labelFor(row.question);
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-current={open ? "page" : undefined}
      aria-label={label}
      title={label}
      className="flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-white aria-[current=page]:bg-white"
    >
      {row.waiting && (
        <span
          data-slot="waiting-dot"
          className="size-1.5 shrink-0 rounded-full bg-waiting"
          aria-hidden
        />
      )}
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate">{label}</span>
        {row.ending && (
          <span data-slot="ending" className="text-xs text-[var(--muted)]">
            {ENDING_WORDS[row.ending]}
          </span>
        )}
      </span>
    </button>
  );
}
