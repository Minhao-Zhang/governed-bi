"use client";

import { Loader2 } from "lucide-react";

import { AgentTimeline } from "@/components/chat/agent-timeline";
import { atLeast, useDisplayMode } from "@/lib/display-mode";
import type { TimelineStep } from "@/lib/steps";

/**
 * The running-progress view. The governed agentic core is the only serve path:
 * streamed and mock turns fold their stage events into `steps`, shown as the live
 * append-only <AgentTimeline/>, which owns the grouping that keeps a twenty-to-
 * thirty-row turn readable. An agent turn before its first event has no rows yet, so it
 * shows a plain indeterminate spinner instead. (There is no non-streaming REST fallback to
 * spin for: `POST /chat` is deleted, and <ChatPanel/> renders <NoTransport/> when
 * `can_stream` is false.)
 *
 * Nothing here keys on a step name. Every stage-name assumption lives in
 * `lib/steps.ts` (the vocabulary) and <AgentTimeline/> (the phases), so a stage
 * the engine renames or adds cannot reach this file.
 *
 * **The trace is `analyst`+, matching the card it turns into.** A step row carries what was
 * retrieved and licensed — physical names included, e.g. "Sampled …employees.role, 10 rows" —
 * which is `analyst`'s line in `lib/display-mode.ts`, not `business`'s. Without the gate the
 * live trace rendered in every mode and then *vanished* when the turn completed, because
 * <AnswerCard/> gates the same <AgentTimeline/> on `analyst`: one turn's worth of detail
 * appearing during and hidden after, which is not a decision anybody made. `business` keeps the
 * spinner, which is what that mode promises — that something is happening, not what.
 *
 * The clarification prompt is not part of this and is never gated: it is a question addressed to
 * the reader, so <ClarificationPrompt/> mounts on its own in every mode.
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
  const mode = useDisplayMode();
  // Two different questions, and they part company exactly while suspended at a clarification.
  // <AgentTimeline/> asks *is this turn unfinished* — it drives the live presentation against
  // the collapsed finished one, so it must stay true through the pause or the trace folds up
  // mid-turn. The spinner below asks *is anything happening*, which is `isRunning` alone.
  const unfinished = isRunning || awaitingClarification;

  if (steps && steps.length > 0 && atLeast(mode, "analyst")) {
    return (
      <AgentTimeline
        steps={steps}
        isRunning={unfinished}
        title="How this answer is being built"
      />
    );
  }
  // A spinner is a claim that something is happening, so it is spelled off `isRunning` and not
  // off "the turn is unfinished". Suspended at a clarification the engine is doing nothing at
  // all — it is waiting on the reader — and the previous version said "Working…" through it.
  if (!isRunning && awaitingClarification) {
    return (
      <p className="text-sm text-muted-foreground">Waiting for your answer.</p>
    );
  }
  // Running, with no timeline to show: an agent turn before its first event, or `business`
  // mode, which is not shown the trace. (Not a REST fallback — there is no such transport.)
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden />
      <span>Working…</span>
    </div>
  );
}
