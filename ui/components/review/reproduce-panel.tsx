"use client";

/**
 * Block 6 of the evidence: does this still happen?
 *
 * **A command, not a button, and that is the honest shape here.** The check re-routes the question
 * through the engine with the agent model off, which needs a warehouse connection and a warm vector
 * cache — neither of which this browser has, and both of which the server would have to be
 * configured for. There is no HTTP verb for it, deliberately: a button that 404'd on most
 * deployments would be worse than a line somebody can copy.
 *
 * **`--embed` is in the command and not optional.** Measured while building the tool: an
 * observation recorded with one missing gold table came back with **two** on a lexical-only
 * re-check, because lexical and embedded retrieval have different coverage ceilings. A "still
 * reproduces" from that is a false positive that reads exactly like a real finding.
 *
 * **What a green result licenses is on the panel, permanently.** The tables the reference answer
 * reads are reachable again — not that the answer is right. On turns where every gold table *was*
 * licensed and the gold names at least one table, measured accuracy is 0.7548 (n=1,150), so about
 * one in four complaints closed on a green check would still come back wrong.
 *
 * The three cases where the check does not apply are named rather than hidden, because a panel
 * offering a command that cannot answer is how somebody concludes the tool is broken.
 */

import { Badge } from "@/components/ui/badge";
import { REVIEW_COPY } from "@/lib/review-copy";
import type { Observation } from "@/lib/types";

/** Why coverage cannot answer this row, or `null`. Mirrors `tools/reproduce_observation.py::_why_not`
 *  — the three sentences are the copy module's, so the CLI and the screen say the same thing. */
function whyNot(observation: Observation): string | null {
  // `undefined` is not `null` here. Absent means this engine is not in steward mode and withheld
  // the field; null means the row has no reference answer. Saying "somebody filed this by hand"
  // to a steward whose engine simply withheld it is a confident wrong answer, which is worse
  // than the parse error this replaced.
  if (observation.gold_sql === undefined) return REVIEW_COPY.reproduceGoldWithheld;
  if (!observation.gold_sql) return REVIEW_COPY.reproduceNoGold;
  if (observation.missing_tables.length === 0) return REVIEW_COPY.reproduceNotCoverage;
  return null;
}

export function ReproducePanel({
  observation,
}: {
  observation: Observation;
}): React.JSX.Element {
  const blocked = whyNot(observation);
  const command = `uv run --frozen python tools/reproduce_observation.py --observation ${observation.observation_id} --embed`;

  return (
    <div className="space-y-2">
      {blocked ? (
        <p className="text-xs text-muted-foreground">{blocked}</p>
      ) : (
        <>
          <p className="text-xs text-muted-foreground">{REVIEW_COPY.reproduceHow}</p>
          <pre className="overflow-x-auto rounded-md bg-muted/40 p-2 text-xs">{command}</pre>
          <p className="text-xs text-muted-foreground">
            <Badge variant="outline" className="mr-1">
              what a pass means
            </Badge>
            {REVIEW_COPY.reproduceClaim}
          </p>
        </>
      )}
    </div>
  );
}
