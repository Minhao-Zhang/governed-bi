"use client";

import { useState } from "react";

import { ModelMarkdown } from "@/components/common/model-markdown";
import type { ClarificationRequest, ClarificationResponse } from "@/lib/clarification";

/**
 * The question the engine asked mid-turn. A choice submits its id; free text
 * submits the words. Decline ends the turn and Defer continues under the stated why.
 * Decline is the only fail-closed control. The server still accepts an older cancel
 * spelling, and this prompt does not send it.
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
  const showFreeform = !hasChoices || request.allow_freeform === true;
  const id = request.clarification_id;

  return (
    <div className="mb-3 space-y-2 text-sm text-[var(--text)]">
      <ModelMarkdown text={request.question} />
      <ModelMarkdown text={request.why} className="text-xs text-[var(--muted)]" />

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

      {showFreeform && (
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
      )}

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
