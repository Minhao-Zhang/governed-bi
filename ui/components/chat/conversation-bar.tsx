"use client";

/**
 * Two actions above the transcript: start a new conversation, or go to the list.
 *
 * **Deliberately does not name the conversation you are in, and does not fetch to find out.**
 * The version this replaces put the whole thread list in a popover here, which needed
 * `useConversations` on the chat page just to render a label — and the label was redundant the
 * moment the transcript loaded, because the first bubble *is* the question. So the chat page
 * fetches no threads at all now; `/history` owns that list and this is a door to it.
 *
 * "New" is disabled when there is no thread open, because that is already a new conversation
 * and a button that does nothing is worse than one that says so.
 */

import Link from "next/link";
import { History, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";

export function ConversationBar({
  threadId,
  onNew,
}: {
  threadId: string | null;
  onNew: () => void;
}) {
  return (
    <div className="flex items-center gap-1">
      <Button variant="ghost" size="sm" onClick={onNew} disabled={threadId === null}>
        <Plus className="size-4" />
        New
      </Button>
      <Button asChild variant="ghost" size="sm">
        <Link href="/history">
          <History className="size-4" />
          History
        </Link>
      </Button>
    </div>
  );
}
