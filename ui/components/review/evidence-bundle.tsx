"use client";

/**
 * What a steward must see on the same screen as the decision.
 *
 * **Six blocks, and the missing one says why it is missing.** The design specified seven. Block 6,
 * the reproducer, is here and costs nothing -- for an imported failure "does this still happen" is a
 * coverage re-check with the answering model off, not a model call.
 * Block 5 ("which corpus assets were in context") and block 7 ("the full record") are absent because
 * an evaluation artifact records neither: `facet_hits`, `pulled_in` and `turn_id` are on **0 of
 * 1,351** rows of the v4 arm, measured. A block rendered empty would read as "we did not bother"
 * rather than "there is no data", so block 5's slot carries the sentence instead.
 *
 * **Block 3 gained something the design had no way to give it: the reference statement, side by
 * side with what the engine produced.** A reader has no gold answer; a benchmark row does, and it is
 * compared by fingerprint rather than read, which makes it the strongest evidence on the page.
 *
 * **The held-out warning is a control and not a caption.** The question text comes from the held-out
 * split, and a person who writes corpus prose from it contaminates the benchmark invisibly.
 * Conformance rule V12 is the gate that catches a verbatim quote; paraphrase leaks cannot be
 * detected at all, so the last line of defence is a reader who knows what they are reading.
 */

import { AlertTriangle } from "lucide-react";

import { ReproducePanel } from "@/components/review/reproduce-panel";
import { SqlBlock } from "@/components/answer/sql-block";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { atLeast, useDisplayMode } from "@/lib/display-mode";
import { CATEGORY_COPY, DECLINE_COPY, REVIEW_COPY, STATE_COPY } from "@/lib/review-copy";
import type { ObservationState } from "@/lib/review-copy";
import type { Observation } from "@/lib/types";

function Section({
  title,
  caption,
  children,
}: {
  title: string;
  caption?: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <section className="space-y-2">
      <div>
        <h3 className="text-sm font-medium">{title}</h3>
        {caption && <p className="text-xs text-muted-foreground">{caption}</p>}
      </div>
      {children}
    </section>
  );
}

export function EvidenceBundle({ observation }: { observation: Observation }): React.JSX.Element {
  const mode = useDisplayMode();
  const stateCopy = STATE_COPY[observation.state as ObservationState];

  return (
    <div className="space-y-6">
      {observation.question_is_held_out && (
        <Card className="flex gap-2 border-destructive/40 bg-destructive/5 p-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
          <p className="text-xs">{REVIEW_COPY.heldOutWarning}</p>
        </Card>
      )}

      {/* 1 — what was asked, and what came back. */}
      <Section title="What was asked, and what came back">
        <p className="text-sm">{observation.question}</p>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {observation.outcome && <Badge variant="outline">{observation.outcome}</Badge>}
          {observation.refused_by && (
            <Badge variant="outline">refused by {observation.refused_by}</Badge>
          )}
          {stateCopy && <Badge variant="secondary">{stateCopy.label}</Badge>}
        </div>
        {stateCopy && <p className="text-xs text-muted-foreground">{stateCopy.sentence}</p>}
        {observation.decline_reason && (
          <p className="text-xs">{DECLINE_COPY[observation.decline_reason] ?? observation.decline_reason}</p>
        )}
        {observation.blocked_note && (
          <p className="text-xs">Waiting on a person: {observation.blocked_note}</p>
        )}
      </Section>

      {/* 2 — what the grader said. The reader's half of the design, replaced by something
              falsifiable: an imported row has no reader, and a fingerprint mismatch is not an
              opinion. */}
      <Section title="What the grader said">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {observation.category && (
            <Badge variant="outline">
              {CATEGORY_COPY[observation.category] ?? observation.category}
            </Badge>
          )}
          {observation.quality_flags.map((flag) => (
            <Badge key={flag} variant="secondary">
              {flag}
            </Badge>
          ))}
        </div>
        {observation.note && <p className="text-sm italic">{observation.note}</p>}
        {observation.gold_fingerprint && (
          <p className="text-xs text-muted-foreground">
            fingerprints — reference <code>{observation.gold_fingerprint}</code>, produced{" "}
            <code>{observation.pred_fingerprint ?? "none"}</code>
          </p>
        )}
      </Section>

      {/* 3 — the statement, beside the reference. */}
      <Section title="The statement">
        {observation.generated_sql ? (
          <SqlBlock sql={observation.generated_sql} />
        ) : (
          <p className="text-xs text-muted-foreground">
            The turn ran no statement, so there is nothing to show. That is its own defect class,
            not a missing field.
          </p>
        )}
      </Section>

      {observation.gold_sql && (
        <Section title={REVIEW_COPY.goldHeading} caption={REVIEW_COPY.goldCaption}>
          <SqlBlock sql={observation.gold_sql} />
        </Section>
      )}

      {/* 4 — what the turn was allowed to read. `schema_ranking` is absent from the artifact, so
              this shows the more direct statement instead: what the reference answer needed and
              did not get. */}
      <Section
        title={REVIEW_COPY.missingTablesHeading}
        caption={REVIEW_COPY.missingTablesCaption}
      >
        {observation.missing_tables.length > 0 ? (
          <ul className="space-y-1 text-sm">
            {observation.missing_tables.map((table) => (
              <li key={table}>
                <code>{table}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">
            None — every table the reference answer reads was reachable, and the answer was still
            wrong. That is a semantics problem rather than a retrieval one, and the free ladder
            cannot see it.
          </p>
        )}
        {atLeast(mode, "engineer") && (
          <p className="text-xs text-muted-foreground">
            allowed to read: {observation.licensed.length ? observation.licensed.join(", ") : "nothing"}
            {observation.schemas.length > 0 && ` · routed: ${observation.schemas.join(", ")}`}
          </p>
        )}
      </Section>

      {/* 5 — the block that cannot exist here, saying so. */}
      <Section title="Which corpus assets were in context">
        <p className="text-xs text-muted-foreground">{REVIEW_COPY.noAssetEvidence}</p>
      </Section>

      {/* 6 — the reproducer. A command rather than a button: the check needs a warehouse and a warm
              vector cache, and a button that 404'd on most deployments would be worse than a line
              somebody can copy. */}
      <Section title={REVIEW_COPY.reproduceHeading}>
        <ReproducePanel observation={observation} />
      </Section>

      {atLeast(mode, "engineer") && (
        <Section title="Provenance">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-muted-foreground">arm</dt>
            <dd>{observation.arm ?? "—"}</dd>
            <dt className="text-muted-foreground">question</dt>
            <dd>{observation.question_id ?? "—"}</dd>
            <dt className="text-muted-foreground">corpus</dt>
            <dd>
              <code>{observation.corpus_content_hash ?? "—"}</code>
            </dd>
            <dt className="text-muted-foreground">filed</dt>
            <dd>{observation.filed_at}</dd>
          </dl>
        </Section>
      )}
    </div>
  );
}
