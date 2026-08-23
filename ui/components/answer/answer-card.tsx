"use client";

import { AlertTriangle, Info } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { ModelMarkdown } from "@/components/common/model-markdown";
import { ReliabilityStamp } from "@/components/answer/reliability-stamp";
import { RaiseNote } from "@/components/answer/raise-note";
import { SqlBlock } from "@/components/answer/sql-block";
import { ProvenanceDrawer } from "@/components/answer/provenance-drawer";
import { AgentTimeline } from "@/components/chat/agent-timeline";
import { useSchemaSummary } from "@/hooks/queries";
import {
  attemptsOf,
  catalogGlimpse,
  corpusVersionLabel,
  deriveDelivery,
  displayText,
  provenanceOf,
  refusalSentence,
  routedSchemasLabel,
  sqlOf,
  terminalLabel,
  terminalOf,
  wantsCatalogGlimpse,
  whyLines,
} from "@/lib/answer-delivery";
import { atLeast, useDisplayMode } from "@/lib/display-mode";
import { buildStepsFromLedger, type TimelineStep } from "@/lib/steps";
import { cn } from "@/lib/utils";
import type { AnswerView } from "@/lib/types";

/**
 * Renders a full `Answer` in one of four states: clean, graded delivery (SQL with an unverified
 * warning), hard refusal, or an ending with no governed statement. Branch on `deriveDelivery` —
 * it owns the mapping from the engine's `outcome` + ledger terminal, and a component reading
 * either directly is a second copy of that rule.
 *
 * **The display mode changes what is shown, never what happened.** All four states render in all
 * three modes and the refusal sentence is present in every one; what widens is the machinery —
 * the SQL, the step trace, the record drawer. A reader in `business` is never shown a turn as
 * having answered when it refused. See `lib/display-mode.ts`, including why this is display and not
 * permission.
 *
 * Two things are *translated* rather than widened, and both go the direction the mode implies:
 * `business` gets `terminalLabel`'s sentence where `engineer` gets the raw `ledger: <token>`
 * badge, and a coverage-claim refusal gets `catalogGlimpse` in every mode. Neither adds or removes
 * a fact — the inputs are `execution.terminal`, the attempt ledger, `refused_by` and the catalog
 * route, all of them things the engine produced.
 */
export function AnswerCard({
  answer,
  steps,
}: {
  answer: AnswerView;
  steps?: TimelineStep[];
}) {
  const mode = useDisplayMode();
  const delivery = deriveDelivery(answer);
  const provenance = provenanceOf(answer);
  const why = whyLines(provenance);
  const schemasNote = routedSchemasLabel(provenance);
  const corpusNote = corpusVersionLabel(provenance);
  const attempts = attemptsOf(answer);
  const refusedBy = answer.refused_by ?? null;
  // The ledger's terminal in plain language, for `business` only. `analyst` and `engineer` are
  // untouched by this: they keep the raw `ledger: <token>` badge, which `reliability-stamp.tsx`
  // gates to `engineer` and which someone who reads ledgers can use. Null above `business` so
  // the token and the sentence can never both be on screen saying the same thing twice.
  const terminalNote = atLeast(mode, "analyst")
    ? null
    : terminalLabel(terminalOf(answer), attempts);
  // Lazily — `enabled` is the whole point. Every other card (every refusal reason outside
  // `CATALOG_GLIMPSE_REFUSALS`, and every answer) leaves this query disabled, so an unconditional
  // fetch on every rendered answer never happens. The key is the same one the schema browser
  // uses, so a reader who has opened that tab already has this cached.
  const needsCatalogGlimpse =
    delivery === "refused" && wantsCatalogGlimpse(refusedBy);
  const schemaSummary = useSchemaSummary(undefined, {
    enabled: needsCatalogGlimpse,
  });
  const glimpse = needsCatalogGlimpse
    ? catalogGlimpse(
        (schemaSummary.data?.items ?? [])
          // Excluded tables are filtered out here, not in `catalogGlimpse`: the exclusion is a
          // property of the corpus row, and a table the model never sees must not be offered as
          // coverage.
          .filter((table) => !table.excluded)
          .map((table) => table.physical_name),
      )
    : null;
  // `semantic_assurance` does not exist in the v2 engine, so the mild-uncertainty banner
  // has no input. Not defaulted to `false` under the old name: that would read as "we
  // checked and it is fine" when nothing checked. The banner is simply gone until something
  // observes uncertainty and records it.
  const sql = sqlOf(answer);
  const text = displayText(answer);
  const turnId =
    typeof answer.record?.turn_id === "string" ? answer.record.turn_id : null;
  // The governed trace, kept on the finished answer so it doesn't vanish: the
  // captured live trace if present, else rebuilt from the ledger (live == audit).
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
        <ReliabilityStamp
          outcome={answer.outcome}
          terminal={terminalOf(answer)}
          attempts={attempts}
          refusedBy={refusedBy}
          mode={mode}
          sentence={refusalSentence(answer)}
        />

        {delivery === "refused" ? (
          <div className="flex gap-3 rounded-md border border-tier-refused/30 bg-tier-refused/5 p-3">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-tier-refused" />
            <p className="text-sm">
              {/* `text` is the system's own copy for this path. `escalation` is gone: the v2
                  engine has no such field, and inventing a sentence here would be the
                  interface speaking for a system that said nothing. */}
              {text ?? "This question can't be answered as asked."}
              {/* What the corpus *can* see, on the refusals whose meaning is a coverage claim
                  (see `CATALOG_GLIMPSE_REFUSALS` for the set and the measurement behind it).
                  In every mode, not just `business`: withholding it from a reader would make
                  the refusal *less* informative for the person who asked than for the person
                  auditing it. Naming tables at `business` is an explicit owner decision —
                  `git-history:docs/analysis/adopting-the-downstream-fork-2026-08-19.md`,
                  third decision: "a refusal may name tables" — so this is not a withholding rule this
                  repository has declined to make, it is one it made the other way. */}
              {glimpse && ` ${glimpse}`}
            </p>
          </div>
        ) : (
          <>
            {/* The stamp's missing half at `business`. On a clean delivery `refusalSentence`
                returns null, so a business reader was told nothing at all about where the
                number came from — and this is the mode whose whole brief is "the answer and
                whether it consulted your data" (`lib/display-mode.ts`). Grouped with the stamp
                and styled like its sentence, because on this delivery it *is* the stamp.

                A standalone line only on `clean`; `no_statement` carries the same phrase inside
                its own panel below. `graded` and `refused` do not get it at all: for `graded`
                `refusalSentence` already says a query ran, and for `refused` the phrase would be
                *wrong* — `serve/nodes/stamp.py::_execution` records `no_sql` "whether it was
                guard-blocked, declined or stubbed", so a guard-blocked refusal carries
                `terminal: no_sql` with an empty ledger and `NO_SQL_LABEL.untouched` would call
                it an answer. */}
            {delivery === "clean" && terminalNote && (
              <p className="text-sm text-muted-foreground">
                This turn {terminalNote}.
              </p>
            )}

            {delivery === "no_statement" && (
              // Informational, not a warning: nothing refused this turn and nothing failed. What
              // a reader needs is that the prose below has no statement under it, which is the
              // one thing the record actually says (`register/stages.py::Outcome.no_sql`).
              <div className="flex gap-3 rounded-md border border-tier-lineage/30 bg-tier-lineage/5 p-3">
                <Info className="mt-0.5 size-4 shrink-0 text-tier-lineage" />
                {/* At `business`, `terminalNote` replaces this copy rather than joining it. It
                    is strictly more precise — it says whether the agent touched the data before
                    answering from a definition, which `no_sql` alone does not (see
                    `NO_SQL_LABEL`) — and it drops "governed statement", which is engine
                    vocabulary this mode exists to keep off the screen. Replacing rather than
                    adding also keeps the count of sentences saying "no query ran" where it was:
                    `refusalSentence` already says it above, in every mode. */}
                <p className="text-sm">
                  {terminalNote
                    ? `This turn ${terminalNote}.`
                    : "This turn ran no query, so there is no governed statement behind the text below."}
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

            {why.length > 0 && (
              <div className="flex gap-3 rounded-md border border-tier-lineage/30 bg-tier-lineage/5 p-3">
                <Info className="mt-0.5 size-4 shrink-0 text-tier-lineage" />
                <ul className="space-y-1 text-sm text-muted-foreground">
                  {why.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Model prose, rendered as markdown — see {@link ModelMarkdown} for what that
                does and does not include. `narrate` adopts the agent's own last message on an
                uncapped turn, so this text is structured the way the agent wrote it: headings,
                code spans and pipe tables, none of which the narrate prompt constrains. */}
            {text && <ModelMarkdown text={text} />}
            {/* No result table. The v2 record does not carry the rows: the engine returns them
                to the model inside the turn and records the statement, not the result set.
                Rendering an empty table would say "the query returned nothing", which is a
                different claim from "we did not keep them". */}
            {/* The statement itself is engineer-only. `refusalSentence` above already told every
                mode whether a query ran, which is the part a reader needs; the SQL is the part
                only someone who reads SQL can use. */}
            {sql && atLeast(mode, "engineer") && <SqlBlock sql={sql} />}
            {/* Which schemas were considered — an analyst's question, not a reader's. */}
            {schemasNote && atLeast(mode, "analyst") && (
              <p className="text-xs text-muted-foreground">{schemasNote}</p>
            )}
            {atLeast(mode, "engineer") && (
              <div className="flex items-center gap-2 pt-1">
                <ProvenanceDrawer provenance={provenance} />
                {/* Which pinned corpus answered — quiet, but on the card: reproducibility is
                    a property of the answer, not of the drawer. */}
                {corpusNote && (
                  <span className="font-mono text-xs text-muted-foreground">
                    {corpusNote}
                  </span>
                )}
              </div>
            )}
          </>
        )}

        {turnId && (
          <RaiseNote
            turnId={turnId}
            kind={delivery === "refused" ? "from_refusal" : "wrong_answer"}
          />
        )}

        {/* Governed step trace in the main thread — starts expanded so routing /
            assembly / corpus hit dropdowns stay visible after the live placeholder
            is replaced. The Provenance sheet keeps the compact collapsed form. */}
        {timeline.length > 0 && atLeast(mode, "analyst") && (
          <div className="border-t pt-3">
            <AgentTimeline steps={timeline} isRunning={false} defaultExpanded />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
