"use client";

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { groupConversations } from "@/components/sidebar/groups";
import { useConversations, usePendingClarifications } from "@/hooks/queries";
import { useThreadId } from "@/hooks/use-thread-id";
import { USE_MOCKS } from "@/lib/env";
import type { PendingClarificationRow } from "@/lib/types";

/** `now` pins the date groups. The page leaves it unset. */
export function Sidebar({ now }: { now?: Date }) {
  const { threadId, setThreadId } = useThreadId();
  const client = useQueryClient();
  const conversations = useConversations();
  const pending = usePendingClarifications();
  const clock = now ?? new Date();

  // A thread change refreshes both lists at once. A run that keeps the same
  // thread shows up on the next poll (hooks/queries.ts).
  const skippedMount = useRef(false);
  useEffect(() => {
    if (USE_MOCKS) return;
    if (!skippedMount.current) {
      skippedMount.current = true;
      return;
    }
    void client.invalidateQueries({ queryKey: ["conversations"] });
    void client.invalidateQueries({ queryKey: ["clarifications", "pending"] });
  }, [threadId, client]);

  const waiting = waitingThreads(pending.data?.rows ?? []);
  const waitingIds = new Set(waiting.map((row) => row.threadId));
  // A waiting row reads as the person's question; the engine's question is the fallback.
  const asked = new Map(
    (conversations.data ?? []).flatMap((row) => (row.question ? [[row.thread_id, row.question]] : [])),
  );
  const groups = groupConversations(
    (conversations.data ?? []).filter((row) => !waitingIds.has(row.thread_id)),
    clock,
  );

  return (
    <aside className="flex h-full min-h-0 w-[260px] shrink-0 flex-col border-r bg-[#f6f6f6] px-2 py-3 text-[var(--text)]">
      <button
        type="button"
        onClick={() => setThreadId(null)}
        className="mb-2 w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-white"
      >
        New conversation
      </button>
      <nav aria-label="Conversations" className="min-h-0 flex-1 overflow-y-auto">
        {waiting.length > 0 && (
          <section aria-labelledby="sidebar-waiting">
            <h2 id="sidebar-waiting" className="px-2 pb-1 text-xs text-[var(--muted)]">
              Waiting for you
            </h2>
            <ul className="flex flex-col gap-0.5">
              {waiting.map((row) => (
                <li key={row.threadId}>
                  <ConversationButton
                    label={labelFor(asked.get(row.threadId) ?? row.question)}
                    open={threadId === row.threadId}
                    waiting
                    onOpen={() => setThreadId(row.threadId)}
                  />
                </li>
              ))}
            </ul>
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
                {group.rows.map((row) => (
                  <li key={row.thread_id}>
                    <ConversationButton
                      label={labelFor(row.question)}
                      open={threadId === row.thread_id}
                      onOpen={() => setThreadId(row.thread_id)}
                    />
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </nav>
    </aside>
  );
}

function waitingThreads(
  rows: PendingClarificationRow[],
): { threadId: string; question: string | null }[] {
  const seen = new Set<string>();
  const out: { threadId: string; question: string | null }[] = [];
  for (const row of rows) {
    const threadId = row.thread_id;
    if (!threadId || seen.has(threadId)) continue;
    seen.add(threadId);
    out.push({ threadId, question: row.question });
  }
  return out;
}

function labelFor(question: string | null): string {
  const text = question?.trim() ?? "";
  return text.length > 0 ? text : "Nothing asked yet";
}

function ConversationButton({
  label,
  open,
  waiting,
  onOpen,
}: {
  label: string;
  open: boolean;
  waiting?: boolean;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-current={open ? "page" : undefined}
      title={label}
      className="flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-white aria-[current=page]:bg-white"
    >
      {waiting && (
        <span
          data-slot="waiting-dot"
          className="size-1.5 shrink-0 rounded-full bg-waiting"
          aria-hidden
        />
      )}
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </button>
  );
}
