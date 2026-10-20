"use client";

import { CheckCircle2, ShieldAlert, ShieldX } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * What the engine observed about this turn: the outcome, and the attempts, because "governance
 * refused every statement" and "no statement was attempted" are different turns. It reports
 * observations only; there is no reliability tier (engine ADR 0007 §3). An unrecognised value
 * shows raw rather than being styled away.
 */

const OUTCOME_CLASSES: Record<string, string> = {
  answered: "bg-tier-governed text-tier-governed-foreground",
  refused: "bg-tier-refused text-tier-refused-foreground",
  clarification: "bg-tier-lineage text-tier-lineage-foreground",
  capped: "bg-tier-fenced-raw text-tier-fenced-raw-foreground",
  crashed: "bg-tier-refused text-tier-refused-foreground",
  // Not the `answered` green: this turn ran no governed statement.
  no_sql: "bg-tier-fenced-raw text-tier-fenced-raw-foreground",
};

export function ReliabilityStamp({
  outcome,
  attempts,
  sentence,
  className,
}: {
  outcome: string;
  attempts: Array<Record<string, unknown>>;
  /** `refusalSentence(answer)` — computed by the caller, which is the one that holds the answer. */
  sentence: string | null;
  className?: string;
}) {
  const passed = attempts.filter((a) => a.passed === true).length;
  const blocked = attempts.length - passed;

  return (
    <div className={cn("space-y-1.5", className)}>
      {sentence && <p className="text-sm text-muted-foreground">{sentence}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          className={cn(
            "font-mono text-xs",
            OUTCOME_CLASSES[outcome] ?? "bg-muted text-foreground",
          )}
        >
          {outcome}
        </Badge>

        {attempts.length === 0 ? (
          // Not "0 blocked": no attempt is a different fact from an attempt that failed.
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <ShieldAlert className="size-3.5" /> no SQL attempted
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            {blocked === 0 ? (
              <CheckCircle2 className="size-3.5 text-tier-governed" />
            ) : (
              <ShieldX className="size-3.5 text-tier-refused" />
            )}
            {passed} passed governance
            {blocked > 0 && `, ${blocked} blocked`}
          </span>
        )}
      </div>
    </div>
  );
}
