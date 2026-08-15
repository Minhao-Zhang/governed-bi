"use client";

/**
 * Serve-time HITL prompt (round 4, hitl-clarification-contract.md). Renders
 * inline in the transcript when a live Analyst run pauses on `ask_user`
 * (`stream.interrupt.value` matches `ClarificationRequest`). Shares the
 * choice-toggles + freeform-input UI with the curator-time admin queue
 * (`components/corpus/clarifications-panel.tsx`, round 2) via
 * `ClarificationAnswerForm`; this component owns only the chat-specific framing
 * (question/why) and submits through `stream.respond(...)` (wired by the
 * caller via `onRespond`), not `POST /clarifications/{id}/answer`.
 */

import { CircleHelp, Clock3, X } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ClarificationRequest, ClarificationResponse } from "@/lib/types";
import { ClarificationAnswerForm } from "@/components/common/clarification-answer-form";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";

export function ClarificationPrompt({
  request,
  submitting,
  onRespond,
  onCancel,
}: {
  request: ClarificationRequest;
  submitting?: boolean;
  onRespond: (response: ClarificationResponse) => void;
  /** Abandon the question. Optional so a caller with no way to unwind a turn (the mock
   * transport) simply does not offer it, rather than offering a button that does nothing. */
  onCancel?: () => void;
}) {
  const hasChoices = !!request.choices && request.choices.length > 0;
  // choices absent => freeform-only (contract §3); choices present => freeform
  // only if allow_freeform is also set.
  const allowFreeform = hasChoices ? request.allow_freeform === true : true;

  return (
    <Card className="border-tier-lineage/40 bg-tier-lineage/5">
      <CardHeader className="[.border-b]:pb-4">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-tier-lineage/15">
            <CircleHelp className="size-4 text-tier-lineage" />
          </span>
          <div className="flex-1 space-y-1.5">
            <CardTitle className="text-sm leading-snug font-medium">{request.question}</CardTitle>
            {request.why && (
              <p className="text-xs leading-relaxed text-muted-foreground">{request.why}</p>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="border-t border-tier-lineage/20 pt-4">
        <ClarificationAnswerForm
          choices={request.choices}
          allowFreeform={allowFreeform}
          submitting={submitting}
          freeformAriaLabel="Free-text answer for the assistant's question"
          onSubmit={(answer) => {
            if (answer.choiceId) {
              onRespond({ clarification_id: request.clarification_id, choice_id: answer.choiceId });
            } else if (answer.answer) {
              onRespond({ clarification_id: request.clarification_id, answer: answer.answer });
            }
          }}
        />
      </CardContent>
      {/* Two ways out, and they are different acts.
         *
         * **Defer** hands the question to an admin and lets the turn finish on the agent's own
         * judgment with its reliability downgraded. It is offered only for `data_definition`
         * questions (objective schema/business-rule facts). A `ranking_ambiguity` question — which
         * metric does "best" mean — is a per-user judgment call, so there is nothing for an admin
         * to answer and the button must never appear. `basis` absent (an older payload, or a future
         * non-`ask_user` caller of this component) fails toward showing it.
         *
         * **Cancel** abandons the question. Always available, and it is the fix for a trap this
         * fork built: the composer is locked while a clarification is pending and `conversation.tsx`
         * removes Stop, so before this existed a `ranking_ambiguity` question — the one basis with
         * no Defer — had no exit but answering. What cancelling costs the admin is decided
         * server-side from the record's own `basis`, so this button carries no policy. */}
      {(request.basis !== "ranking_ambiguity" || onCancel) && (
        <CardFooter className="justify-end gap-2 border-t border-tier-lineage/20 bg-transparent pt-3">
          {onCancel && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={!!submitting}
              className="gap-1.5 text-muted-foreground hover:text-foreground"
              onClick={onCancel}
            >
              <X className="size-3.5" />
              Never mind — cancel this question
            </Button>
          )}
          {request.basis !== "ranking_ambiguity" && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!!submitting}
              className={cn(
                "gap-1.5 border-dashed text-muted-foreground",
                "hover:bg-tier-lineage/10 hover:text-tier-lineage",
              )}
              onClick={() => onRespond({ clarification_id: request.clarification_id, defer: true })}
            >
              <Clock3 className="size-3.5" />
              I don&apos;t know — ask the admin later
            </Button>
          )}
        </CardFooter>
      )}
    </Card>
  );
}
