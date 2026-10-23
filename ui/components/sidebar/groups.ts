/**
 * Calendar-day buckets. Yesterday is its own heading, so "Previous 7 days"
 * is two to seven days ago and "Previous 30 days" is eight to thirty.
 */

import type { ConversationSummary } from "@/lib/threads";

const DATE_GROUPS = [
  "Today",
  "Yesterday",
  "Previous 7 days",
  "Previous 30 days",
  "Older",
] as const;

export type DateGroup = (typeof DATE_GROUPS)[number];

export function groupConversations(
  rows: ConversationSummary[],
  now: Date,
): { title: DateGroup; rows: ConversationSummary[] }[] {
  const buckets = new Map<DateGroup, ConversationSummary[]>(
    DATE_GROUPS.map((title) => [title, []]),
  );
  for (const row of rows) buckets.get(conversationGroup(row.updated_at, now))!.push(row);
  return DATE_GROUPS.flatMap((title) => {
    const group = buckets.get(title)!;
    return group.length > 0 ? [{ title, rows: group }] : [];
  });
}

function conversationGroup(updatedAt: string, now: Date): DateGroup {
  const at = new Date(updatedAt);
  if (Number.isNaN(at.getTime())) return "Older";
  const days = calendarDaysBefore(now, at);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days <= 7) return "Previous 7 days";
  if (days <= 30) return "Previous 30 days";
  return "Older";
}

/** Local calendar days, via UTC midnight, so a DST day still counts as one. */
function calendarDaysBefore(now: Date, then: Date): number {
  const utcDay = (d: Date) => Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((utcDay(now) - utcDay(then)) / 86_400_000);
}
