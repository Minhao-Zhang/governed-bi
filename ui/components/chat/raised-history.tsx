"use client";

/**
 * "What you've raised" (detent-ai-trust-loop-plan.md, task B-2) -- the reader's own history of
 * what this thread raised, and what became of it. The plan's minimum: "the reader's own history
 * shows 'you asked about X; an admin defined it on <date>', and re-asking now works."
 *
 * **The surface decision, and the argument for it.** Mounted beside `ConversationBar` (task B-2
 * lives in `stream-chat.tsx`), not as a separate page or nav item. Weighed against two other
 * candidates:
 *
 * - **A separate "history" page.** `/history` already exists and already means something else
 *   (`lib/threads.ts`: the *server's* threads, not the caller's) -- and it is deliberately absent
 *   from the `business` tier's reachable set (`capabilities.ts::REACHABLE`), because listing every
 *   thread on the server would let a business reader read other people's questions. This app has
 *   no identity concept (`api/routes.py::_identity` falls back to the thread id), so "the
 *   reader's own history" can only mean *this thread's* history, and the one place a business
 *   reader can reach at all is `/` (`REACHABLE.business = ["/"]`). A second page for a thread-
 *   scoped view of one thing this reader is already looking at would be a page for its own sake.
 * - **A per-answer-card note, correlating this turn's own licensed assets against what this
 *   thread raised.** Considered and rejected: it needs the engine to report, per turn, which
 *   licensed asset (if any) traces back to a clarification/report *this same thread* filed --
 *   which is a new correlation nothing computes today, and the plan's own "no new engine fields"
 *   principle (stated for task I, and no less true here) argues against inventing one for B. I-3
 *   already gives a re-asked question its own signal ("answered from a definition, without
 *   running a query") without this; B's job is narrower -- confirming *whose* raised concept it
 *   was, which this list already does.
 *
 * So: one quiet, thread-scoped list, mounted exactly where a reader would go to re-ask --
 * `ConversationBar` sits right above the transcript and the composer, on the one page a business
 * reader can reach. It satisfies both halves of the plan's minimum at once: the reader sees "an
 * admin has since defined it" here, and can immediately re-ask in the box below it.
 *
 * **Business tier only** (`tierShowsRaisedHistory`), same argument as
 * `RefusalClarificationPrompt`/`WrongAnswerReport`: an analyst/engineer can already see this
 * directly on `/corpus` (Reports, Drafts, Assumptions), so this is the narrower substitute for a
 * surface a business reader cannot reach.
 *
 * **Only the `certified` case renders — silence otherwise, and that silence is chosen, not
 * accidental.** `GET /threads/{id}/raised` (task B-1) reports every raised item's real status,
 * including `open` and `dismissed`, but this component filters to `certified === true` only.
 * A dismissed report carries no admin-visible reason (`curator/feedback.py::dismiss_report`
 * takes none), so surfacing a bare "an admin dismissed this" would read as an unexplained
 * rejection -- worse than the silence H-3's own same-turn "an admin will see this" already left
 * the reader with. "Still open" adds nothing beyond that same acknowledgment. Nothing here polls,
 * badges, or otherwise pushes this at the reader (constraint: this must not become a
 * notifications system) -- it renders once, quietly, only when there is genuinely good news.
 *
 * **No fabricated date.** The plan's own phrasing is "an admin defined it on <date>"; this
 * component does not print a `<date>` for the certification half of that sentence, because the
 * engine stamps none anywhere (`corpus/drafts.py::approve_draft`; see `trust_loop_routes.py`'s
 * own docstring). The one honest date available is when the reader raised it
 * (`FeedbackRecord.reported_at`), which is not what "on <date>" was asking for, so it is left out
 * rather than substituted in.
 */

import { useCapabilities, useRaisedByThread } from "@/hooks/queries";
import { resolveTier, tierShowsRaisedHistory } from "@/lib/capabilities";
import { useDisplayModeOverride } from "@/lib/display-mode";

export function RaisedHistory({ threadId }: { threadId: string | null }) {
  const { data: caps } = useCapabilities();
  const tier = resolveTier(caps, useDisplayModeOverride());
  const shows = tierShowsRaisedHistory(tier);
  const { data } = useRaisedByThread(threadId, { enabled: shows });

  if (!shows) return null;
  const defined = (data ?? []).filter((item) => item.certified);
  if (defined.length === 0) return null;

  return (
    <div className="space-y-1 rounded-md border border-tier-lineage/30 bg-tier-lineage/5 p-3 text-xs text-muted-foreground">
      {defined.map((item) => (
        <p key={item.id}>
          You asked about &ldquo;{item.question}&rdquo; — an admin has since defined it. Ask
          again and this answer will use that definition.
        </p>
      ))}
    </div>
  );
}
