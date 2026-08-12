"use client";

import { Loader2 } from "lucide-react";

import { AgentTimeline } from "@/components/chat/agent-timeline";
import type { TimelineStep } from "@/lib/steps";

/**
 * The running-progress view. The governed agentic core is the only serve path:
 * streamed and mock turns fold their stage events into `steps`, shown as the live
 * append-only <AgentTimeline/>, which owns the grouping that keeps a twenty-to-
 * thirty-row turn readable. The non-streaming REST fallback has no live stream, so
 * it — or an agent turn before its first event — shows a plain indeterminate
 * spinner instead.
 *
 * Nothing here keys on a step name. Every stage-name assumption lives in
 * `lib/steps.ts` (the vocabulary) and <AgentTimeline/> (the phases), so a stage
 * the engine renames or adds cannot reach this file.
 */
export function ServeProgress({
  isRunning,
  steps,
}: {
  isRunning: boolean;
  steps?: TimelineStep[];
}) {
  if (steps && steps.length > 0) {
    return (
      <AgentTimeline
        steps={steps}
        isRunning={isRunning}
        title="How this answer is being built"
      />
    );
  }
  // No timeline events yet (REST fallback, or before the first agent event).
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden />
      <span>Working…</span>
    </div>
  );
}
