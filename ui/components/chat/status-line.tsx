"use client";

import { currentStage, statusSentence } from "@/lib/status-line";
import type { TimelineStep } from "@/lib/steps";

/** The one grey line under the question while a turn is running. */
export function StatusLine({ steps }: { steps?: readonly TimelineStep[] }) {
  return (
    <p role="status" className="text-sm text-[var(--muted)]">
      {statusSentence(currentStage(steps ?? []))}
    </p>
  );
}
