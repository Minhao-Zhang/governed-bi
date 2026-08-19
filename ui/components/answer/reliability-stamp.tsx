import { CheckCircle2, ShieldAlert, ShieldX } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * What the engine observed about this turn.
 *
 * **Rewritten for the v2 engine, and the deletions are the point.** This component used to
 * render a two-axis stamp from `tier` + `safety_clearance` + `semantic_assurance`. None of
 * those exists in v2 — the reliability-tier concept was deliberately not carried across the
 * rewrite (engine ADR 0007 §3). The tempting fix was to default `tier` to `"governed"` and
 * keep the badge. That would put a reliability claim with nothing behind it on the most
 * prominent element of the interface, which is the exact class of defect the rewrite removed:
 * a field reporting a *configuration* rather than an *observation*, so a broken turn and a
 * clean one look identical.
 *
 * So this reports only what the engine actually records:
 *
 * - `outcome` — answered / refused / clarification / capped / crashed / no_sql.
 * - `terminal` — the ledger's own terminal state, which the engine now derives from the
 *   attempts rather than from whether a string was produced.
 * - the **attempts**, because "governance refused every statement" and "no statement was ever
 *   attempted" are different turns that used to render identically.
 *
 * The old `TIER_CLASSES` / `TIER_LABEL` maps were **unchecked record indexes**, so an
 * unrecognised value rendered `undefined` as a class and a blank label. That is not carried
 * forward: every lookup here falls back explicitly and shows the raw value, on the principle
 * that an unfamiliar state should be visible rather than invisible.
 */

const OUTCOME_CLASSES: Record<string, string> = {
  answered: "bg-tier-governed text-tier-governed-foreground",
  refused: "bg-tier-refused text-tier-refused-foreground",
  clarification: "bg-tier-lineage text-tier-lineage-foreground",
  capped: "bg-tier-fenced-raw text-tier-fenced-raw-foreground",
  crashed: "bg-tier-refused text-tier-refused-foreground",
  // Not the `answered` green. This turn ran no governed statement, and the badge sits directly
  // beside `ledger: no_sql` and "no SQL attempted" — the three used to disagree, with the badge
  // the only one of them claiming an answer.
  no_sql: "bg-tier-fenced-raw text-tier-fenced-raw-foreground",
};

export function ReliabilityStamp({
  outcome,
  terminal,
  attempts,
  refusedBy,
  className,
}: {
  outcome: string;
  terminal: string | null;
  attempts: Array<Record<string, unknown>>;
  refusedBy: string | null;
  className?: string;
}) {
  const passed = attempts.filter((a) => a.passed === true).length;
  const blocked = attempts.length - passed;

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      {/* Falls back to the raw string rather than to a default class: an outcome this build
          does not recognise is something a reader should see, not something to style away. */}
      <Badge
        className={cn("font-mono text-xs", OUTCOME_CLASSES[outcome] ?? "bg-muted text-foreground")}
      >
        {outcome}
      </Badge>

      {/* The ledger's terminal, beside the outcome precisely so a disagreement between them is
          visible. Both are computed from the same attempts and should agree — the engine's own
          contract asserts it — and this is where a reader would notice if they stopped. */}
      {terminal && (
        <Badge variant="outline" className="font-mono text-xs">
          ledger: {terminal}
        </Badge>
      )}

      {attempts.length === 0 ? (
        // Not "0 blocked": no attempt is a different fact from an attempt that failed, and
        // collapsing the two is how a refusal used to read as an answer.
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

      {refusedBy && (
        <Badge variant="outline" className="font-mono text-xs text-tier-refused">
          refused by {refusedBy}
        </Badge>
      )}
    </div>
  );
}
