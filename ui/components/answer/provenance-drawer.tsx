"use client";

import { FileSearch } from "lucide-react";

import { AgentTimeline } from "@/components/chat/agent-timeline";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  formatProvenanceValue,
  groupProvenance,
  stageEvents,
  stagesTotalMs,
  type StageEvent,
} from "@/lib/provenance";
import { buildStepsFromLedger } from "@/lib/steps";
import { cn } from "@/lib/utils";

/**
 * The per-answer audit surface. Three parts, in the order a reviewer works:
 *
 *  1. **Steps** — the governed loop replayed from the recorded ledger, through the
 *     same renderer the live run used (live == audit).
 *  2. **Stage timings** — `stage_events` as readable rows rather than a JSON blob.
 *  3. **Grouped key/values** — governance first, then stage counters and the ADR
 *     0004 run record (both collapsed: operator metadata, not governance).
 *
 * The grouping lives in `lib/provenance.ts`; see the note there on why a flat dump
 * stopped working once run logging started stamping ~21 keys per answer.
 */
export function ProvenanceDrawer({ provenance }: { provenance: Record<string, unknown> }) {
  const groups = groupProvenance(provenance);
  // `execution`, the same field <AnswerCard/> rebuilds from. It was
  // `governance_ledger`, which the v2 record does not have at all — so this read
  // named nothing and the section could only ever be empty.
  //
  // It is still empty, for a different and honest reason, and this one is a known
  // engine gap rather than a client bug: `execution` is an *object*
  // (`{terminal, attempts, guardrail_errors}` — an attempt ledger),
  // `buildStepsFromLedger` takes an array, and `execution.attempts` rows carry no
  // `step`, so there is nothing here to name a row after. **The record does not
  // contain the stage events**, only the turn's attempts, so it cannot rebuild the
  // trace the stream showed. Every streamed turn *is* logged now, so `/audit/turns`
  // is populated; the missing piece is specifically a stage-event array, and that
  // is a feature nobody has built rather than a wire-up that was forgotten.
  //
  // Until it exists the live trace kept on the finished turn (`ChatMessage.steps`)
  // is the only trace, so "live == audit" is an intention rather than a fact, and a
  // turn whose live trace was missed — a reload, a reconnect, another tab — has no
  // step trace at all.
  const ledgerSteps = buildStepsFromLedger(provenance.execution);
  const stages = stageEvents(provenance.stage_events);

  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5">
          <FileSearch className="size-3.5" />
          Provenance
        </Button>
      </SheetTrigger>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Provenance</SheetTitle>
          <SheetDescription>The audit trace for this answer.</SheetDescription>
        </SheetHeader>

        {ledgerSteps.length > 0 && (
          <section className="border-b px-4 pb-4">
            <h3 className="mb-2 text-xs font-medium text-muted-foreground">Steps</h3>
            <AgentTimeline
              steps={ledgerSteps}
              isRunning={false}
              title="Governed steps"
              preferOpenDetails={false}
            />
          </section>
        )}

        {stages.length > 0 && <StageTimings stages={stages} />}

        <div className="px-4 pb-6">
          {groups.map((group) => {
            // `stage_events` has its own section above — drop the redundant blob
            // row, but only when that section actually rendered. If the value is
            // present yet unparseable, keep the raw row so the audit loses nothing.
            const entries =
              stages.length > 0
                ? group.entries.filter(([key]) => key !== "stage_events")
                : group.entries;
            if (entries.length === 0) return null;
            return (
              <ProvenanceSection
                key={group.id}
                title={group.title}
                entries={entries}
                defaultOpen={!group.collapsed}
              />
            );
          })}
        </div>
      </SheetContent>
    </Sheet>
  );
}

/** `stage_events` as one row per stage: name, status, duration. */
function StageTimings({ stages }: { stages: StageEvent[] }) {
  const total = stagesTotalMs(stages);

  return (
    <section className="border-b px-4 pb-4">
      <h3 className="mb-2 text-xs font-medium text-muted-foreground">
        Stage timings{total > 0 && <span className="tabular-nums"> · {Math.round(total)} ms</span>}
      </h3>
      <ul className="space-y-1">
        {stages.map((stage, i) => (
          <li
            key={`${stage.stage}:${i}`}
            className="flex items-baseline justify-between gap-2 text-xs"
          >
            <span className="truncate font-mono">{stage.stage}</span>
            <span className="flex shrink-0 items-baseline gap-2">
              {stage.status !== "ok" && (
                <span
                  className={cn(
                    stage.status === "error" ? "text-tier-refused" : "text-muted-foreground",
                  )}
                >
                  {stage.status}
                </span>
              )}
              {/* `ms: null` means the stage was skipped, not that it was instant. */}
              <span className="tabular-nums text-muted-foreground">
                {stage.ms === null ? "—" : `${Math.round(stage.ms)} ms`}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ProvenanceSection({
  title,
  entries,
  defaultOpen,
}: {
  title: string;
  entries: [string, unknown][];
  defaultOpen: boolean;
}) {
  return (
    <details open={defaultOpen} className="border-b py-3 last:border-b-0">
      <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
        {title}
        <span className="ml-1.5 font-normal tabular-nums">({entries.length})</span>
      </summary>
      <dl className="mt-3 grid gap-3">
        {entries.map(([key, value]) => (
          <div key={key} className="grid grid-cols-[9rem_1fr] gap-2 border-b pb-2 text-sm">
            <dt className="font-mono text-xs text-muted-foreground">{key}</dt>
            <dd className="break-words font-mono text-xs">{formatProvenanceValue(key, value)}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
