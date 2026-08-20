"use client";

import { useState } from "react";
import { MessageCircleQuestion } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ModelMarkdown } from "@/components/common/model-markdown";
import type { ClarificationRequest, ClarificationResponse } from "@/lib/clarification";
import { cn } from "@/lib/utils";

/**
 * The serve-time clarification (HITL) prompt. When the governed agent interrupts
 * mid-turn to ask one question, this renders the active surface for it
 * (docs/plans/hitl-clarification-contract.md §3): the question, the WHY line
 * (governance transparency — the user always sees why they're asked), and the
 * answer controls per the request shape:
 *
 *  - `choices` present  → option buttons; `allow_freeform` also offers a text box.
 *  - `choices` absent    → freeform textarea only.
 *
 * Answering resumes the same turn; Decline fails the turn closed (contract §4).
 *
 * The gate is the **arriving interrupt**, not `capabilities.can_clarify`. A server
 * that interrupts is by definition able to clarify, and an interrupt the user has
 * no control to answer deadlocks the turn — so a stale/false capability flag must
 * not be able to hide this prompt. The flag stays advisory (it says whether to
 * *expect* clarifications), which is why nothing branches on it here.
 *
 * Shares `clarification_id` with the passive "Asked a question…" timeline row.
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
    <div className="mb-2 rounded-lg border border-primary/40 bg-primary/5 p-3">
      <div className="flex items-start gap-2">
        <MessageCircleQuestion className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
        <div className="min-w-0 flex-1 space-y-2">
          {/* Both of these are model-written — `ask_user`'s own arguments — so they get the
              same renderer as the answer card rather than a bare `<p>`, which showed a
              backtick-quoted column name as three literal characters in the one sentence
              whose job is telling a reader what they are being asked. */}
          <ModelMarkdown text={request.question} className="font-medium" />
          <ModelMarkdown
            text={request.why}
            className="text-xs text-muted-foreground"
          />

          {hasChoices && (
            <div className="flex flex-col gap-1.5">
              {request.choices!.map((choice) => (
                <Button
                  key={choice.id}
                  variant="outline"
                  size="sm"
                  className="justify-start"
                  onClick={() => onRespond({ clarification_id: id, choice_id: choice.id })}
                >
                  {choice.label}
                </Button>
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
                className={cn(
                  "flex max-h-32 min-h-8 w-full resize-y rounded-md border border-input bg-background px-2.5 py-1.5 text-sm outline-none transition-colors",
                  "placeholder:text-muted-foreground",
                  "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
                )}
              />
              <Button type="submit" size="sm" disabled={text.trim() === ""} className="shrink-0">
                Answer
              </Button>
            </form>
          )}

          <div className="pt-0.5">
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="text-muted-foreground"
              onClick={() => onRespond({ clarification_id: id, declined: true })}
            >
              Decline
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
