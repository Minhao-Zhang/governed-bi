"use client";

/** "New" above the transcript; disabled when no thread is open, since that is already new. */

import { Plus } from "lucide-react";

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
    </div>
  );
}
