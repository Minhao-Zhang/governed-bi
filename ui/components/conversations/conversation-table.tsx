"use client";

import Link from "next/link";
import { useState } from "react";

import { labelFor } from "@/components/sidebar/sidebar";
import { useConversations } from "@/hooks/queries";
import { ENDINGS, ENDING_WORDS } from "@/lib/endings";
import type { ConversationRow, Ending } from "@/lib/types";

/** The newest 500 cover the store at this volume; the page says when there are more. */
const PAGE_LIMIT = 500;

/** Every conversation as a plain table, newest first, with a filter by ending. */
export function ConversationTable() {
  const [ending, setEnding] = useState<Ending | null>(null);
  const conversations = useConversations({ limit: PAGE_LIMIT, ending });
  const rows = conversations.data?.rows ?? [];
  const total = conversations.data?.meta.total ?? 0;

  return (
    <div className="mx-auto w-full max-w-[960px] px-6 py-8 text-[var(--text)]">
      <div className="mb-6 flex items-baseline justify-between gap-4">
        <h1 className="text-xl">Conversations</h1>
        <Link href="/" className="text-sm text-[var(--muted)] hover:text-[var(--text)]">
          Back to chat
        </Link>
      </div>
      <label className="mb-4 flex items-center gap-2 text-sm text-[var(--muted)]">
        How it ended
        <select
          value={ending ?? ""}
          onChange={(event) => setEnding((event.target.value || null) as Ending | null)}
          className="rounded-md border px-2 py-1 text-[var(--text)]"
        >
          <option value="">Any</option>
          {ENDINGS.map((value) => (
            <option key={value} value={value}>
              {ENDING_WORDS[value]}
            </option>
          ))}
        </select>
      </label>
      {conversations.isError ? (
        <p className="text-sm text-[var(--muted)]">The conversations could not be loaded.</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-[var(--muted)]">
          {conversations.isLoading ? "Loading…" : "No conversations."}
        </p>
      ) : (
        <table className="w-full border-collapse text-left text-sm">
          <thead className="text-xs text-[var(--muted)]">
            <tr className="border-b">
              <th className="py-2 pr-4 font-normal">First question</th>
              <th className="py-2 pr-4 font-normal">Turns</th>
              <th className="py-2 pr-4 font-normal">How it ended</th>
              <th className="py-2 font-normal">Date</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <ConversationLine key={row.thread_id ?? row.updated_at} row={row} />
            ))}
          </tbody>
        </table>
      )}
      {total > rows.length && (
        <p className="mt-3 text-xs text-[var(--muted)]">
          Showing {rows.length} of {total}.
        </p>
      )}
    </div>
  );
}

function ConversationLine({ row }: { row: ConversationRow }) {
  const first = row.turns[0]?.question ?? row.question;
  // A waiting turn is not recorded until it finishes, but the person has asked it.
  const turns = row.elided_turns + row.turns.length + (row.waiting ? 1 : 0);
  const label = labelFor(first);
  return (
    <tr className="border-b hover:bg-[var(--bubble)]">
      <td className="max-w-0 py-2 pr-4">
        {row.thread_id ? (
          <Link
            href={`/?thread=${encodeURIComponent(row.thread_id)}`}
            className="block truncate"
            title={label}
          >
            {label}
          </Link>
        ) : (
          <span className="block truncate">{label}</span>
        )}
      </td>
      <td className="py-2 pr-4 tabular-nums">{turns}</td>
      <td className="py-2 pr-4">{row.ending ? ENDING_WORDS[row.ending] : ""}</td>
      <td className="py-2 whitespace-nowrap text-[var(--muted)]">{dateOf(row.updated_at)}</td>
    </tr>
  );
}

function dateOf(iso: string | null): string {
  const at = new Date(iso ?? "");
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
