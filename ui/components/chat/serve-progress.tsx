"use client";

import { Loader2 } from "lucide-react";

import { AgentTimeline } from "@/components/chat/agent-timeline";
import type { TimelineStep } from "@/lib/steps";

/**
 * The running view: the live <AgentTimeline/> once stage events arrive, a spinner before the
 * first one, and "Waiting for your answer." while suspended at a clarification. Every
 * stage-name assumption lives in `lib/steps.ts` and <AgentTimeline/>, not here.
 */
export function ServeProgress({
  isRunning,
  steps,
  awaitingClarification = false,
}: {
  isRunning: boolean;
  steps?: TimelineStep[];
  /** Suspended at a clarification: the engine is waiting on the reader, not working. */
  awaitingClarification?: boolean;
}) {
  // The timeline asks "is this turn unfinished", which stays true through a clarification so the
  // trace does not fold up mid-turn; the spinner asks "is anything happening", `isRunning` alone.
  const unfinished = isRunning || awaitingClarification;

  if (steps && steps.length > 0) {
    return (
      <AgentTimeline
        steps={steps}
        isRunning={unfinished}
        title="How this answer is being built"
      />
    );
  }
  if (!isRunning && awaitingClarification) {
    return (
      <p className="text-sm text-muted-foreground">Waiting for your answer.</p>
    );
  }
  // Running, before the first stage event.
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden />
      <span>Working…</span>
    </div>
  );
}
