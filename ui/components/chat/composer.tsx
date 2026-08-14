"use client";

import { useState, type KeyboardEvent } from "react";
import { SendHorizontal, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The question input. Enter submits; Shift+Enter inserts a newline. While a turn
 * is running the Send button becomes a Stop button (when the transport can abort)
 * so a long agent loop or stream can be cancelled. Uses a native, auto-growing
 * textarea styled to match the shadcn Input (no Textarea primitive in the set).
 */
export function Composer({
  onSend,
  isRunning = false,
  onStop,
}: {
  onSend: (text: string) => void;
  isRunning?: boolean;
  onStop?: () => void;
}) {
  const [value, setValue] = useState("");

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || isRunning) return;
    onSend(trimmed);
    setValue("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  const canStop = isRunning && onStop !== undefined;

  return (
    <form
      className="flex items-end gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={onKeyDown}
        disabled={isRunning}
        rows={1}
        placeholder="Ask a question about the governed data…"
        aria-label="Ask a question about the governed data"
        className={cn(
          "flex max-h-40 min-h-9 w-full resize-y rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm outline-none transition-colors",
          "placeholder:text-muted-foreground",
          "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
          "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
        )}
      />
      {canStop ? (
        <Button type="button" variant="outline" onClick={onStop} className="shrink-0">
          <Square className="size-4" />
          Stop
        </Button>
      ) : (
        <Button type="submit" disabled={isRunning || value.trim() === ""} className="shrink-0">
          <SendHorizontal className="size-4" />
          Send
        </Button>
      )}
    </form>
  );
}
