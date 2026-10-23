"use client";

import { useState } from "react";

import type { ClarificationRequest, ClarificationResponse } from "@/lib/clarification";

/**
 * The engine's question in plain text, with outlined choices and a free-text
 * answer. Defer continues under the stated why. Decline is the only fail-closed
 * control, so this prompt does not send the older cancel spelling.
 */
export function ClarificationPrompt({
  request,
  onRespond,
}: {
  request: ClarificationRequest;
  onRespond: (response: ClarificationResponse) => void;
}) {
  const [text, setText] = useState("");
  const hasChoices = !!request.choices && request.choices.length > 0;
  const id = request.clarification_id;

  return (
    <div className="mb-3 space-y-2 text-sm text-[var(--text)]">
      <p className="whitespace-pre-wrap">{request.question}</p>
      <p className="whitespace-pre-wrap text-[var(--muted)]">{request.why}</p>

      {hasChoices && (
        <div className="flex flex-col gap-1.5">
          {request.choices!.map((choice) => (
            <button
              key={choice.id}
              type="button"
              className="rounded-lg border border-black/15 px-3 py-1.5 text-left text-sm"
              onClick={() => onRespond({ clarification_id: id, choice_id: choice.id })}
            >
              {choice.label}
            </button>
          ))}
        </div>
      )}

      <form
        className="flex items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          const trimmed = text.trim();
          if (!trimmed) return;
          onRespond({ clarification_id: id, answer: trimmed });
          setText("");
        }}
      >
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              (event.currentTarget.form as HTMLFormElement)?.requestSubmit();
            }
          }}
          rows={1}
          placeholder={hasChoices ? "Or answer in your own words…" : "Type your answer…"}
          aria-label="Answer the clarification"
          className="max-h-32 min-h-8 w-full resize-none rounded-lg border border-black/15 bg-transparent px-2.5 py-1.5 text-sm text-[var(--text)] outline-none placeholder:text-[var(--muted)]"
        />
        <button
          type="submit"
          disabled={text.trim() === ""}
          className="shrink-0 rounded-lg border border-black/15 px-3 py-1.5 text-sm disabled:opacity-40"
        >
          Answer
        </button>
      </form>

      <div className="flex flex-wrap gap-3 pt-0.5">
        <button
          type="button"
          className="text-xs text-[var(--muted)]"
          onClick={() => onRespond({ clarification_id: id, deferred: true })}
        >
          Defer
        </button>
        <button
          type="button"
          className="text-xs text-[var(--muted)]"
          onClick={() => onRespond({ clarification_id: id, declined: true })}
        >
          Decline
        </button>
      </div>
    </div>
  );
}
