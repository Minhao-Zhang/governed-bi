"use client";

import { AlertTriangle, Info } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { ReliabilityStamp } from "@/components/answer/reliability-stamp";
import { SqlBlock } from "@/components/answer/sql-block";
import { ProvenanceDrawer } from "@/components/answer/provenance-drawer";
import { AgentTimeline } from "@/components/chat/agent-timeline";
import {
  attemptsOf,
  corpusVersionLabel,
  deriveDelivery,
  displayText,
  provenanceOf,
  refusalSentence,
  routedSchemasLabel,
  sqlOf,
  terminalOf,
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
  // `semantic_assurance` does not exist in the v2 engine, so the mild-uncertainty banner
  // has no input. Not defaulted to `false` under the old name: that would read as "we
  // checked and it is fine" when nothing checked. The banner is simply gone until something
  // observes uncertainty and records it.
  const sql = sqlOf(answer);
  const text = displayText(answer);
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
          attempts={attemptsOf(answer)}
          refusedBy={answer.refused_by ?? null}
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
            </p>
          </div>
        ) : (
          <>
            {delivery === "no_statement" && (
              // Informational, not a warning: nothing refused this turn and nothing failed. What
              // a reader needs is that the prose below has no statement under it, which is the
              // one thing the record actually says (`register/stages.py::Outcome.no_sql`).
              <div className="flex gap-3 rounded-md border border-tier-lineage/30 bg-tier-lineage/5 p-3">
                <Info className="mt-0.5 size-4 shrink-0 text-tier-lineage" />
                <p className="text-sm">
                  This turn ran no query, so there is no governed statement
                  behind the text below.
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

            {text && <Narration text={text} />}
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

/** One `| a | b |` row, split into trimmed cells with the outer pipes dropped. */
function cellsOf(line: string): string[] {
  return line
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim());
}

/** `|---|:--|--:|` — the row that makes the line above it a header. */
const TABLE_RULE = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/;

/**
 * Renders the narration's block structure: paragraphs, the `- ` lists a model writes when the
 * answer enumerates rows, and pipe tables. Inline formatting is {@link Emphasised} — bold only.
 *
 * The reason this exists at all is that a one-`<p>` render collapses every newline, so an answer
 * shaped as a list arrives as one run-on line with the hyphens still in it. The blank-line and
 * bullet structure the model wrote is the answer's structure; dropping it is a misread, not a
 * simplification.
 *
 * **Tables are here because the card is the only place a result can appear.** The record does
 * not carry the rows (see the comment at the call site), so a model asked for the answer to a
 * question with a result set writes it as a pipe table into `answer_text` — there is no other
 * channel. Without this branch that arrived as `| zip_code | / |---:| / | 652 |`, four literal
 * pipes on the most-read line of the app.
 *
 * **Still not a markdown renderer,** for the reason in {@link Emphasised}: paragraphs, bullets,
 * bold, tables. Each is a construct models actually emit here, hand-rendered, and cells go
 * through `Emphasised` like every other run of text, so a table cell is no more a rendering
 * surface than a paragraph is. Line breaks inside a paragraph are preserved rather than folded
 * the way markdown folds them, so a construct this doesn't know — a numbered list, an indented
 * note — at least keeps the shape the model gave it instead of being run together.
 */
function Narration({ text }: { text: string }) {
  type Block =
    | { kind: "ul"; items: string[] }
    | { kind: "p"; lines: string[] }
    | {
        kind: "table";
        head: string[];
        rows: string[][];
        align: ("left" | "right")[];
      };
  const blocks: Block[] = [];
  let open: Block | null = null;
  const lines = text.trim().split("\n");
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].trim();
    // A blank line ends whatever was open; the next content starts a fresh block.
    if (!line) {
      open = null;
      continue;
    }
    // A header row is only a header if the next line is the rule; otherwise `| a | b |` is
    // ordinary prose that happens to contain pipes, and stays prose.
    const next = (lines[i + 1] ?? "").trim();
    if (open?.kind !== "table" && line.includes("|") && TABLE_RULE.test(next)) {
      const head = cellsOf(line);
      blocks.push(
        (open = {
          kind: "table",
          head,
          rows: [],
          // `--:` means the column is numeric, which is most of them here.
          align: cellsOf(next).map((c) => (c.endsWith(":") ? "right" : "left")),
        }),
      );
      i += 1;
      continue;
    }
    if (open?.kind === "table") {
      if (line.includes("|")) {
        open.rows.push(cellsOf(line));
        continue;
      }
      open = null;
    }
    const bullet = /^[-*+]\s+(.*)$/.exec(line);
    if (bullet) {
      if (open?.kind !== "ul") blocks.push((open = { kind: "ul", items: [] }));
      open.items.push(bullet[1]);
    } else {
      if (open?.kind !== "p") blocks.push((open = { kind: "p", lines: [] }));
      open.lines.push(line);
    }
  }

  return (
    <div className="space-y-2 text-sm leading-relaxed">
      {blocks.map((block, b) => {
        if (block.kind === "ul") {
          return (
            <ul key={b} className="list-disc space-y-1 pl-5">
              {block.items.map((item, i) => (
                <li key={i}>
                  <Emphasised text={item} />
                </li>
              ))}
            </ul>
          );
        }
        if (block.kind === "table") {
          return (
            <div key={b} className="overflow-x-auto">
              <table className="w-auto border-collapse text-sm tabular-nums">
                <thead>
                  <tr className="border-b">
                    {block.head.map((cell, i) => (
                      <th
                        key={i}
                        className={`px-3 py-1.5 font-medium text-muted-foreground ${
                          block.align[i] === "right"
                            ? "text-right"
                            : "text-left"
                        }`}
                      >
                        <Emphasised text={cell} />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {block.rows.map((row, r) => (
                    <tr key={r} className="border-b last:border-0">
                      {row.map((cell, i) => (
                        <td
                          key={i}
                          className={`px-3 py-1.5 ${
                            block.align[i] === "right"
                              ? "text-right"
                              : "text-left"
                          }`}
                        >
                          <Emphasised text={cell} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
        return (
          <p key={b} className="whitespace-pre-line">
            <Emphasised text={block.lines.join("\n")} />
          </p>
        );
      })}
    </div>
  );
}

/**
 * Renders `**bold**` and nothing else.
 *
 * The narration is model prose, and a model answering "how many restaurants are there" writes
 * *"There are **9,590 restaurants** in total."* — it bolds the number that answers the
 * question, which is the most useful emphasis in the app and rendered as four literal
 * asterisks on the single most-read line.
 *
 * **Bold only, deliberately, and not a markdown renderer.** A general one is a dependency and a
 * surface: this text comes from a model, so every construct a renderer supports is a construct
 * the model can emit into the answer card — links, images, headings, HTML. The narrate prompt
 * asks for one or two sentences, and the one piece of formatting that actually appears in them
 * is bold. Anything else stays literal, which is visible and harmless, where a silently
 * rendered anchor would not be.
 */
function Emphasised({ text }: { text: string }) {
  // Split on the delimiter and keep it, so odd-indexed parts are the bolded runs. An unmatched
  // `**` therefore lands on an even index and renders as text, which is the right failure.
  const parts = text.split(/\*\*(.+?)\*\*/g);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <strong key={i}>{part}</strong>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}
