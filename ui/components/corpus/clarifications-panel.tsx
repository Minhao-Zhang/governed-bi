"use client";

/**
 * Admin clarification queue (round 2 of the SME hand-off UI). Lists every
 * unanswered record from the curator's `clarifications.jsonl` (GET
 * /clarifications) and lets the admin answer one: pick a `choices` entry
 * (rendered as toggle buttons) and/or, when `allow_freeform`, type free text.
 * Submitting POSTs `{choice_id?, answer?}` to `/clarifications/{id}/answer`
 * (gated on `capabilities.can_curate_corpus`, not `can_edit` -- see the same
 * fix `conflicts-panel.tsx` already got in ef52743).
 *
 * **Two statuses are homework, not one.** This asked for `?status=open` while
 * that was the only unanswered state a record could be in. It no longer is:
 * a live `ask_user` the user deferred now lands `deferred`
 * (curator/clarifications.py::close_live_clarification), so an exact-match
 * `open` filter would have made every deferred question -- the ones a user
 * explicitly handed to an admin -- vanish from the admin's queue. Fetching
 * unfiltered and naming the two homework states here is cheaper than teaching
 * the route a multi-value filter, and it fails loudly rather than silently if a
 * fourth status ever appears: the new state shows up in neither list until
 * somebody decides which one it belongs in.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CircleHelp, ClipboardList, MessagesSquare, UserRoundX } from "lucide-react";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api-client";
import { canCurateCorpus } from "@/lib/capabilities";
import type { ClarificationRecord } from "@/lib/types";
import { useCapabilities, useClarifications } from "@/hooks/queries";
import { ClarificationAnswerForm } from "@/components/common/clarification-answer-form";
import { QueryState } from "@/components/common/query-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** The statuses that mean "an admin still owes this an answer". */
const HOMEWORK: ReadonlyArray<ClarificationRecord["status"]> = ["open", "deferred"];

const isHomework = (record: ClarificationRecord) => HOMEWORK.includes(record.status);

export function ClarificationsPanel() {
  const clarifications = useClarifications();
  const { data: caps } = useCapabilities();
  // Same fix as conflicts-panel.tsx (ef52743): POST /clarifications/{id}/answer is not
  // gated on can_edit on the backend (mirrors /corpus/drafts/{id}/approve's pattern
  // exactly -- can_edit is a hard-coded /capabilities report for the unrelated free-form
  // corpus editor). Gating this form on canEdit made it unreachable in every real
  // deployment, since can_edit is hard-coded false in v2 today.
  const editable = canCurateCorpus(caps);

  return (
    <QueryState
      query={clarifications}
      isEmpty={(data) => data.filter(isHomework).length === 0}
      emptyMessage="No clarifications waiting on an answer."
    >
      {(data) => (
        <div className="space-y-3">
          {data.filter(isHomework).map((record) => (
            <ClarificationCard key={record.id} record={record} editable={editable} />
          ))}
        </div>
      )}
    </QueryState>
  );
}

function ClarificationCard({
  record,
  editable,
}: {
  record: ClarificationRecord;
  editable: boolean;
}) {
  const queryClient = useQueryClient();
  const [submitting, setSubmitting] = useState(false);

  async function submit(answer: { choiceId?: string; answer?: string }) {
    if (submitting) return;
    setSubmitting(true);
    try {
      await api.answerClarification(record.id, answer);
      toast.success(`Answered ${record.id}`);
      await queryClient.invalidateQueries({ queryKey: ["clarifications"] });
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to submit the answer.";
      toast.error(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-2">
          <CircleHelp className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="flex-1 space-y-1">
            <CardTitle className="text-sm leading-snug font-medium">
              {record.question}
            </CardTitle>
            <p className="font-mono text-xs text-muted-foreground">{record.scope}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {record.status === "deferred" && <DeferredBadge />}
            <SourceBadge source={record.source} />
            <Badge variant="outline" className="text-muted-foreground">
              {record.id}
            </Badge>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        <ClarificationAnswerForm
          choices={record.choices}
          allowFreeform={record.allow_freeform}
          disabled={!editable}
          submitting={submitting}
          freeformAriaLabel={`Free-text answer for ${record.id}`}
          onSubmit={(answer) => void submit(answer)}
        />
        {!editable && (
          <p className="text-xs text-muted-foreground">
            Answering requires a connected dev backend (`capabilities.can_edit`).
          </p>
        )}
      </CardContent>
    </Card>
  );
}

/** A user was asked this live and handed it to you, rather than nobody having
 * seen it yet. Worth its own badge because the two are the same amount of work
 * but not the same priority: somebody is waiting on an answer they already
 * asked for, and the turn that asked shipped with its reliability downgraded
 * until this lands. */
function DeferredBadge() {
  return (
    <Badge variant="secondary" className="text-amber-700 dark:text-amber-500">
      <UserRoundX className="size-3" />
      Deferred to you
    </Badge>
  );
}

/** Where the question came from: raised offline during corpus review, or
 * asked mid-conversation by an `ask_user` interrupt in the live chat
 * (round 6). Orthogonal to `DeferredBadge` above — a live-chat question may be
 * freshly in flight *or* deferred, and only the latter earns both. */
function SourceBadge({ source }: { source: ClarificationRecord["source"] }) {
  if (source === "live_chat") {
    return (
      <Badge variant="secondary" className="text-muted-foreground">
        <MessagesSquare className="size-3" />
        From live chat
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-muted-foreground">
      <ClipboardList className="size-3" />
      From corpus review
    </Badge>
  );
}
