"use client";

/**
 * Admin report queue (detent-ai-trust-loop-plan.md, task H-4) -- every `open` `FeedbackRecord` a
 * reader filed through `components/answer/wrong-answer-report.tsx`. Lists `GET
 * /feedback?status=open` and lets the admin either answer it (`POST /feedback/{id}/answer`,
 * which folds the correction into a `proposed` corpus draft through the same Enhancer path
 * task A/D's own clarification-answer route already uses) or dismiss it (`POST
 * /feedback/{id}/dismiss`, no corpus change).
 *
 * **A second inbox, not a second queue to hunt for (H-c).** Same admin surface (`/corpus`'s tab
 * bar), a new "Reports" tab beside Clarifications -- H-b already decided the *record* must not
 * merge with the clarification ledger (different lifecycle: a report cannot be deferred;
 * different meaning: the engine asking vs. the reader objecting), and this panel is the UI half
 * of keeping that distinction real rather than presenting one merged list.
 *
 * Modeled directly on `clarifications-panel.tsx`: same `canCurateCorpus` gate (not `can_edit` --
 * none of `POST /feedback/*` is gated on it either, mirroring every sibling admin route in
 * `curation_routes.py`), same card-per-record layout, same toast-on-submit +
 * invalidate-the-query pattern. The correction input reuses `ClarificationAnswerForm` in its
 * freeform-only mode (no `choices`), the same shared component `clarifications-panel.tsx` uses
 * for its own freeform answers -- one submit interaction for "type an answer, then submit",
 * not a second implementation of it.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { MessageSquareWarning } from "lucide-react";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api-client";
import { canCurateCorpus } from "@/lib/capabilities";
import type { FeedbackRecord } from "@/lib/types";
import { useCapabilities, useFeedback } from "@/hooks/queries";
import { ClarificationAnswerForm } from "@/components/common/clarification-answer-form";
import { QueryState } from "@/components/common/query-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function FeedbackPanel() {
  const feedback = useFeedback("open");
  const { data: caps } = useCapabilities();
  // Same fix conflicts-panel.tsx/clarifications-panel.tsx already carry: none of POST
  // /feedback/{id}/answer|dismiss is gated on can_edit on the backend (mirrors
  // /corpus/drafts/{id}/approve's pattern exactly -- can_edit is a hard-coded /capabilities
  // report for the unrelated free-form corpus editor). Gating this panel on it would build a
  // fourth control that can never render, since can_edit is hard-coded false in v2 today.
  const editable = canCurateCorpus(caps);

  return (
    <QueryState
      query={feedback}
      isEmpty={(data) => data.length === 0}
      emptyMessage="No reports waiting on a response."
    >
      {(data) => (
        <div className="space-y-3">
          {data.map((record) => (
            <FeedbackCard key={record.id} record={record} editable={editable} />
          ))}
        </div>
      )}
    </QueryState>
  );
}

function FeedbackCard({ record, editable }: { record: FeedbackRecord; editable: boolean }) {
  const queryClient = useQueryClient();
  const [submitting, setSubmitting] = useState(false);

  async function invalidate() {
    await queryClient.invalidateQueries({ queryKey: ["feedback"] });
  }

  async function submitCorrection(correction: string) {
    if (submitting) return;
    setSubmitting(true);
    try {
      await api.answerFeedback(record.id, correction);
      toast.success(`Answered ${record.id}`);
      await invalidate();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to submit the correction.";
      toast.error(message);
    } finally {
      setSubmitting(false);
    }
  }

  async function dismiss() {
    if (submitting) return;
    setSubmitting(true);
    try {
      await api.dismissFeedback(record.id);
      toast.success(`Dismissed ${record.id}`);
      await invalidate();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to dismiss the report.";
      toast.error(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-2">
          <MessageSquareWarning className="mt-0.5 size-4 shrink-0 text-tier-refused" />
          <div className="flex-1 space-y-1">
            <CardTitle className="text-sm leading-snug font-medium">{record.question}</CardTitle>
            <p className="text-xs text-muted-foreground">
              Answered: <span className="italic">&ldquo;{record.answer_text}&rdquo;</span>
            </p>
            {record.reason && (
              <p className="text-xs text-muted-foreground">Reader says: {record.reason}</p>
            )}
          </div>
          <Badge variant="outline" className="shrink-0 text-muted-foreground">
            {record.id}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        <ClarificationAnswerForm
          choices={null}
          allowFreeform
          disabled={!editable}
          submitting={submitting}
          submitLabel="Submit correction"
          freeformAriaLabel={`Correction for ${record.id}`}
          freeformPlaceholder="What's the correct answer?"
          onSubmit={(answer) => {
            if (answer.answer) void submitCorrection(answer.answer);
          }}
        />
        <div className="flex justify-end">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={!editable || submitting}
            onClick={() => void dismiss()}
          >
            Dismiss — no corpus change needed
          </Button>
        </div>
        {!editable && (
          <p className="text-xs text-muted-foreground">
            Responding requires a connected dev backend (`capabilities.can_curate_corpus`).
          </p>
        )}
      </CardContent>
    </Card>
  );
}
