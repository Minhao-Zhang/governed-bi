"use client";

import { AlertTriangle, Info } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { ModelMarkdown } from "@/components/common/model-markdown";
import { OutcomeBadge } from "@/components/answer/outcome-badge";
import { AgentTimeline } from "@/components/chat/agent-timeline";
import {
  attemptsOf,
  deriveDelivery,
  displayText,
  provenanceOf,
  refusalSentence,
} from "@/lib/answer-delivery";
import { buildStepsFromLedger, type TimelineStep } from "@/lib/steps";
import { cn } from "@/lib/utils";
import type { AnswerView } from "@/lib/types";

/**
 * Renders a full `Answer` in one of four states: clean, graded, refused, or an ending with no
 * governed statement. Branch on `deriveDelivery`: it owns the mapping from the engine's
 * `outcome` and ledger terminal, and a component reading either directly is a second copy.
 */
export function AnswerCard({
  answer,
  steps,
}: {
  answer: AnswerView;
  steps?: TimelineStep[];
}) {
  const delivery = deriveDelivery(answer);
  const provenance = provenanceOf(answer);
  const text = displayText(answer);
  // The captured live trace if present, else rebuilt from the ledger, so it outlives the run.
  const timeline =
    steps && steps.length > 0
      ? steps
      : buildStepsFromLedger(provenance.execution);

  return (
    <Card
      className={cn(
        delivery === "graded" &&
          "border-tier-fenced-raw/50 bg-tier-fenced-raw/5",
      )}
    >
      <CardContent className="space-y-3 pt-0">
        <OutcomeBadge
          outcome={answer.outcome}
          attempts={attemptsOf(answer)}
          sentence={refusalSentence(answer)}
        />

        {delivery === "refused" ? (
          <div className="flex gap-3 rounded-md border border-tier-refused/30 bg-tier-refused/5 p-3">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-tier-refused" />
            {/* `text` is the system's own copy for this path; nothing is invented when it is absent. */}
            <p className="text-sm">{text ?? "This question can't be answered as asked."}</p>
          </div>
        ) : (
          <>
            {delivery === "no_statement" && (
              // Informational, not a warning: nothing refused this turn and nothing failed.
              <div className="flex gap-3 rounded-md border border-tier-lineage/30 bg-tier-lineage/5 p-3">
                <Info className="mt-0.5 size-4 shrink-0 text-tier-lineage" />
                <p className="text-sm">
                  This turn ran no query, so there is no governed statement behind the text below.
                </p>
              </div>
            )}

            {delivery === "graded" && (
              <div className="space-y-2 rounded-md border border-tier-fenced-raw/40 bg-tier-fenced-raw/10 p-3">
                <div className="flex gap-3">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0 text-tier-fenced-raw" />
                  <p className="text-sm font-medium">
                    We produced this answer but could not fully verify it.
                  </p>
                </div>
              </div>
            )}

            {text && <ModelMarkdown text={text} />}
            {/* No result table: the record keeps the statement, not the rows, and an empty
                table would claim the query returned nothing. */}
          </>
        )}

        {/* Starts expanded so the trace, SQL included, stays visible once the live view is replaced. */}
        {timeline.length > 0 && (
          <div className="border-t pt-3">
            <AgentTimeline steps={timeline} isRunning={false} defaultExpanded />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
