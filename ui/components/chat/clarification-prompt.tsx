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

import { CircleHelp, Clock3 } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ClarificationRequest, ClarificationResponse } from "@/lib/types";
import { ClarificationAnswerForm } from "@/components/common/clarification-answer-form";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";

export function ClarificationPrompt({
  request,
  submitting,
  onRespond,
}: {
  request: ClarificationRequest;
  submitting?: boolean;
  onRespond: (response: ClarificationResponse) => void;
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
      {/* Deferring to an admin only makes sense for data_definition questions
          (objective schema/business-rule facts). ranking_ambiguity questions
          (subjective, per-user judgment calls) must never offer it. `basis`
          absent (older payload shape, or a future non-ask_user caller of this
          component) fails toward today's behavior: show the button. */}
      {request.basis !== "ranking_ambiguity" && (
        <CardFooter className="justify-end gap-2 border-t border-tier-lineage/20 bg-transparent pt-3">
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
        </CardFooter>
      )}
    </Card>
  );
}
