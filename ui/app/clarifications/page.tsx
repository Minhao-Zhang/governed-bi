import { PageShell } from "@/components/layout/page-shell";
import { PendingQueueSurface } from "@/components/clarifications/pending-queue";

/**
 * `/clarifications` — questions the engine asked and nobody answered.
 *
 * **Its own route rather than a section of `/audit`.** That page's own description is "every turn
 * this server has served", and these are precisely the ones it did not: a clarification nobody
 * answered never became a turn, so it appears in no audit row. Reading order differs too — a log is
 * newest-first, a queue oldest-first — and putting both scroll directions on one screen makes each
 * worse.
 */
export default function ClarificationsPage() {
  return (
    <PageShell
      title="Pending questions"
      description="Clarifications the engine asked that nobody has answered, oldest first. Read-only — answering still happens in the conversation that asked."
    >
      <PendingQueueSurface />
    </PageShell>
  );
}
