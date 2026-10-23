"use client";

import { useState, type KeyboardEvent } from "react";
import { SendHorizontal } from "lucide-react";

/**
 * The question input. Enter sends; Shift+Enter inserts a line. While a turn
 * is running, Stop replaces Send.
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
    <div>
      <form
        className="flex items-end gap-2 rounded-3xl border border-black/10 bg-white px-3 py-2"
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
          className="max-h-40 min-h-6 w-full resize-none bg-transparent py-1 text-sm text-[var(--text)] outline-none placeholder:text-[var(--muted)] disabled:opacity-50"
        />
        {canStop ? (
          <button
            type="button"
            onClick={onStop}
            className="shrink-0 rounded-full px-3 py-1 text-sm text-[var(--muted)]"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            aria-label="Send"
            disabled={isRunning || value.trim() === ""}
            className="flex size-8 shrink-0 items-center justify-center rounded-full bg-[var(--text)] text-white disabled:opacity-30"
          >
            <SendHorizontal className="size-4" aria-hidden />
          </button>
        )}
      </form>
      <p className="mt-2 text-xs text-[var(--muted)]">
        Read-only. Every query is checked before it runs.
      </p>
    </div>
  );
}
