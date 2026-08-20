"use client";

/**
 * `/audit` — what this server actually did, turn by turn.
 *
 * Two things, and both are about **runs**:
 *
 *   1. **Every served turn, across every thread** — the durable log. A turn's record
 *      reaches its own client once, in the run that produced it; until the engine started
 *      appending turns there was no way to see one again, so the governance ledger and the
 *      licensed set were produced and dropped.
 *   2. **One turn, stage by stage** — grouped by the pipeline stage that *owns* each
 *      recorded field, read from the engine's own record register. Not a layout
 *      written here: a field added to the register appears in this page with no
 *      change to this file, and a section can never claim a stage the register does
 *      not assign.
 *
 * `incomplete_fields` is the column that matters most and is easy to miss: a turn
 * whose record is missing a *required* field is not a turn that worked, however good
 * its answer looks.
 *
 * **Corpus state used to be a third section here and has moved to `/corpus`**
 * (`components/corpus/corpus-status.tsx`). It described the corpus, not a run, and it was
 * the third surface doing so — the `/health` page, deleted in the same change, was the
 * second. What is left on this page is the only thing on it that is about runs.
 *
 * Note what this page is a tail *of*: an append-only JSONL under `runs/serve/`, one
 * installation's own traffic. The experiment ledger is a different file with a different
 * contract, and this is a debugging surface rather than a measurement one.
 */

import { useState } from "react";
import { CheckCircle2, ChevronRight, Info, ShieldX } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { useAuditTrace, useAuditTurns } from "@/hooks/queries";
import type {
  AuditLedgerRow,
  AuditTrace,
  AuditTraceField,
  AuditTurnSummary,
  AuditTurns,
} from "@/lib/types";
import { QueryState } from "@/components/common/query-state";
import { ModelMarkdown } from "@/components/common/model-markdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export function AuditSurface() {
  const [selected, setSelected] = useState<string | null>(null);

  return (
    // **Two scroll boxes, not one page.** Selecting a turn used to append the trace below the
    // list and make the whole page grow, so the row you clicked slid away and reading the trace
    // meant scrolling past a 500-row table to get back to it. Split the height instead: the list
    // keeps the top half, the trace takes the bottom half, and each scrolls on its own.
    <div className="flex h-full min-h-0 flex-col gap-6">
      <TurnsPanel selected={selected} onSelect={setSelected} />
      {selected && <TracePanel turnId={selected} />}
    </div>
  );
}

/* ── the turn list ────────────────────────────────────────────────────────── */

/** The turn list's columns. **Filterable on every one**, and each says how to read a value
 * out of a summary row — so the filter, the sort and the cell all use one accessor and
 * cannot disagree about what a column means.
 *
 * Client-side rather than server-side, unlike the corpus table: a turn log is one developer's
 * traffic, not 13,981 assets, and `/audit/turns` is already a bounded newest-first tail. A
 * `where=` contract here would be a second filter language for a smaller problem. */
const TURN_COLUMNS: {
  key: string;
  label: string;
  align?: "right";
  kind: "text" | "number";
  /** A Tailwind width cap. **Load-bearing, not decoration** — see the note below. */
  width?: string;
  get: (t: AuditTurnSummary) => string | number | null;
}[] = [
  { key: "asked_at", label: "Asked", kind: "text", width: "w-[8.5rem]", get: (t) => t.asked_at },
  { key: "question", label: "Question", kind: "text", width: "max-w-[22rem]", get: (t) => t.question },
  { key: "outcome", label: "Outcome", kind: "text", width: "w-28", get: (t) => t.outcome },
  {
    key: "terminal_reason",
    label: "Reason",
    width: "max-w-[11rem]",
    kind: "text",
    get: (t) => t.terminal_reason,
  },
  { key: "schemas", label: "Schemas", kind: "text", width: "max-w-[11rem]", get: (t) => (t.schemas ?? []).join(",") },
  {
    key: "licensed_count",
    width: "w-20",
    label: "Licensed",
    align: "right",
    kind: "number",
    get: (t) => t.licensed_count,
  },
  {
    key: "attempts",
    label: "Attempts",
    align: "right",
    kind: "number",
    width: "w-20",
    get: (t) => t.attempts,
  },
  {
    key: "incomplete_fields",
    width: "w-20",
    label: "Record",
    align: "right",
    kind: "number",
    get: (t) => t.incomplete_fields,
  },
  { key: "generated_sql", label: "SQL", kind: "text", width: "max-w-[26rem]", get: (t) => t.generated_sql },
];

function TurnsPanel({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<string>("asked_at");
  const [desc, setDesc] = useState(true);

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-3">
      <SectionTitle icon={Info} title="Served turns" />
      <QueryState
        query={useAuditTurns(500)}
        isEmpty={(data: AuditTurns) => data.turns.length === 0}
        emptyMessage="No turns have been served yet."
        skeleton={<Skeleton className="h-48 w-full" />}
      >
        {(data) => {
          const active = Object.entries(filters).filter(([, v]) => v.trim() !== "");
          const shown = data.turns
            .filter((turn) =>
              active.every(([key, needle]) => {
                const column = TURN_COLUMNS.find((c) => c.key === key);
                const value = column ? column.get(turn) : null;
                return String(value ?? "").toLowerCase().includes(needle.trim().toLowerCase());
              }),
            )
            .sort((a, b) => {
              const column = TURN_COLUMNS.find((c) => c.key === sort) ?? TURN_COLUMNS[0];
              const left = column.get(a);
              const right = column.get(b);
              const cmp =
                column.kind === "number"
                  ? Number(left ?? 0) - Number(right ?? 0)
                  : String(left ?? "").localeCompare(String(right ?? ""));
              return desc ? -cmp : cmp;
            });

          return (
            <>
              <div className="flex min-h-0 flex-1 flex-col rounded-md border">
                {/* One scroll container for both axes, so the header can stick to it. The
                    header carries a filter input per column — the control most worth keeping on
                    screen while you scroll a 500-row tail. */}
                <Table containerClassName="min-h-0 flex-1 overflow-auto">
                  <TableHeader className="sticky top-0 z-20 bg-background [&_tr]:border-b-0 [&_th]:shadow-[inset_0_-1px_0_var(--border)]">
                    <TableRow>
                      {TURN_COLUMNS.map((column) => (
                        <TableHead
                          key={column.key}
                          // The same cap as the body cell. A header left to size itself would
                          // widen the column right back — a table column is as wide as its
                          // widest cell, header included.
                          className={cn("align-top", column.width)}
                        >
                          <div className={cn("flex flex-col gap-1.5 py-1", column.align === "right" && "items-end text-right")}>
                            <button
                              type="button"
                              onClick={() => {
                                if (sort === column.key) setDesc(!desc);
                                else {
                                  setSort(column.key);
                                  setDesc(false);
                                }
                              }}
                              className={cn(
                                "font-mono text-[11px] hover:text-foreground",
                                sort === column.key && "text-foreground",
                              )}
                            >
                              {column.label}
                              {sort === column.key ? (desc ? " ↓" : " ↑") : ""}
                            </button>
                            <Input
                              value={filters[column.key] ?? ""}
                              onChange={(e) =>
                                setFilters((prev) => ({ ...prev, [column.key]: e.target.value }))
                              }
                              placeholder="filter"
                              className="h-6 w-full min-w-0 px-1.5 text-[11px]"
                              aria-label={`${column.label} filter`}
                            />
                          </div>
                        </TableHead>
                      ))}
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {shown.map((turn, i) => (
                      <TurnRow
                        // Composite, and **never `Math.random()`** — which was the fallback
                        // here. A key that changes every render makes React unmount and
                        // remount the row each time, so the list flickered and lost focus on
                        // any refetch. `turn_id` alone is not enough either: it was minted
                        // without the thread, so two conversations asking one question shared
                        // an id and React reported a duplicate key and dropped a row. The
                        // engine now separates them; this key also holds for rows logged
                        // before that fix.
                        key={`${turn.turn_id ?? "no-id"}|${turn.asked_at ?? ""}|${i}`}
                        turn={turn}
                        active={turn.turn_id === selected}
                        onSelect={onSelect}
                      />
                    ))}
                  </TableBody>
                </Table>
              </div>
              <p className="flex shrink-0 items-center gap-2 font-mono text-[11px] text-muted-foreground">
                {/* "of N" only when a filter is on, so a filtered count and a total count are
                    never the same-looking number. */}
                {shown.length}
                {active.length > 0 ? ` of ${data.turns.length} matching` : " turns"} ·{" "}
                {data.meta.log_dir}
                {active.length > 0 && (
                  <Button variant="ghost" size="xs" onClick={() => setFilters({})}>
                    clear
                  </Button>
                )}
              </p>
            </>
          );
        }}
      </QueryState>
    </section>
  );
}

function TurnRow({
  turn,
  active,
  onSelect,
}: {
  turn: AuditTurnSummary;
  active: boolean;
  onSelect: (id: string | null) => void;
}) {
  const id = turn.turn_id;
  return (
    <TableRow
      className={cn("cursor-pointer", active && "bg-muted/60")}
      onClick={() => onSelect(active ? null : id)}
    >
      {/* Driven by TURN_COLUMNS, not a hand-written cell list. The header renders from the
          same array, so a column added there cannot leave the body one cell short — which is
          exactly what happened when the header grew from six columns to nine and the row did
          not, silently shifting every value one column left. */}
      {TURN_COLUMNS.map((column) => (
        <TableCell
          key={column.key}
          className={cn(
            "align-top",
            // **`whitespace-normal` is the fix, and the width cap alone would not have been
            // one.** `TableCell` ships `whitespace-nowrap` (shadcn's default), which makes the
            // `line-clamp-2` on the cell's text inert — clamping needs the text to wrap, so
            // instead every SQL statement rendered on one unbroken line. Measured: the SQL
            // column alone was 2659px and the table 4728px inside a 663px container, so rows
            // ran under the next section and the page looked like it was overlapping itself.
            // The clamp was always there and always meant this; nothing was reaching it.
            "whitespace-normal break-words",
            column.width,
            column.align === "right" && "text-right",
            column.kind === "number" && "font-mono text-xs",
          )}
        >
          <TurnCell column={column.key} turn={turn} value={column.get(turn)} />
        </TableCell>
      ))}
      <TableCell>
        <ChevronRight
          className={cn("size-4 text-muted-foreground transition", active && "rotate-90")}
        />
      </TableCell>
    </TableRow>
  );
}

/** One cell. Three columns get a purpose-built rendering because their *meaning* is not the
 * string: an outcome is a trust signal, a record count is a pass/fail, and a timestamp reads
 * better without the ISO punctuation. Everything else prints its accessor's value, so adding
 * a column to `TURN_COLUMNS` needs nothing here. */
function TurnCell({
  column,
  turn,
  value,
}: {
  column: string;
  turn: AuditTurnSummary;
  value: string | number | null;
}) {
  if (column === "outcome") {
    return <OutcomeBadge outcome={turn.outcome} />;
  }
  if (column === "incomplete_fields") {
    // A turn whose record is missing a required field is not a turn that worked, whatever
    // the answer looked like. It is the least visible failure here, so it gets a red count
    // rather than a footnote.
    return turn.incomplete_fields === 0 ? (
      <CheckCircle2 className="ml-auto size-4 text-tier-governed" />
    ) : (
      <span className="font-mono text-xs text-tier-refused">−{turn.incomplete_fields}</span>
    );
  }
  if (column === "attempts") {
    return (
      <span className="font-mono text-xs">
        {turn.attempts_passed}/{turn.attempts}
      </span>
    );
  }
  if (column === "asked_at") {
    return (
      <span className="whitespace-nowrap font-mono text-[11px] text-muted-foreground">
        {String(value ?? "—").replace("T", " ").replace("+00:00", "Z")}
      </span>
    );
  }
  if (value === null || value === undefined || value === "") {
    return <span className="text-muted-foreground">—</span>;
  }
  return (
    <span
      className={cn(
        "line-clamp-2 text-[11px]",
        column === "question" ? "text-sm" : "",
        column === "generated_sql" || column === "schemas" ? "font-mono" : "",
      )}
      title={String(value).length > 80 ? String(value) : undefined}
    >
      {String(value)}
    </span>
  );
}

function OutcomeBadge({
  outcome,
  reason,
}: {
  outcome: string | null;
  reason?: string | null;
}) {
  const tone =
    outcome === "answered"
      ? "border-tier-governed/40 text-tier-governed"
      : outcome === "crashed"
        ? "border-tier-refused/40 text-tier-refused"
        : "border-tier-lineage/40 text-tier-lineage";
  return (
    <Badge variant="outline" className={cn("font-mono text-[11px]", tone)}>
      {outcome ?? "—"}
      {reason ? ` · ${reason}` : ""}
    </Badge>
  );
}

/* ── one turn, stage by stage ─────────────────────────────────────────────── */

function TracePanel({ turnId }: { turnId: string }) {
  return (
    <section className="flex min-h-0 flex-1 flex-col gap-3">
      <SectionTitle icon={Info} title={`Trace · ${turnId}`} />
      {/* A trace is a dozen stage cards deep and has to scroll somewhere. Here, not on the page:
          `pr-1` keeps the scrollbar off the cards' right edge. */}
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        <QueryState
          query={useAuditTrace(turnId)}
          isEmpty={(data: AuditTrace) => !data.found}
          emptyMessage="That turn is not in the log."
          skeleton={<Skeleton className="h-64 w-full" />}
        >
          {(trace) => <TraceBody trace={trace} />}
        </QueryState>
      </div>
    </section>
  );
}

function TraceBody({ trace }: { trace: AuditTrace }) {
  return (
    <div className="flex flex-col gap-4">
      {trace.question && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-normal">{trace.question}</CardTitle>
            {/* The answer is model prose and renders as markdown, same as the answer card.
                `trace.question` above stays literal: it is what the *reader* typed, and
                rendering a user's own words as markup is a separate decision nobody made. */}
            {trace.answer_text && (
              <CardDescription>
                <ModelMarkdown text={trace.answer_text} />
              </CardDescription>
            )}
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-center gap-2">
              <OutcomeBadge outcome={trace.outcome ?? null} />
              {trace.terminal && (
                <Badge variant="outline" className="font-mono text-[11px]">
                  terminal · {trace.terminal}
                </Badge>
              )}
              {trace.missing_required.length > 0 && (
                <Badge
                  variant="outline"
                  className="border-tier-refused/40 font-mono text-[11px] text-tier-refused"
                >
                  {trace.missing_required.length} required field(s) absent
                </Badge>
              )}
              {/* The mirror of the badge above, and the rarer, more interesting failure: not a
                  declared field the run failed to write, but a field the run wrote that
                  nothing declared. The stage list cannot show it — it is built FROM the
                  declaration — so without this the register quietly stops describing the
                  engine. Neutral styling: an undeclared key is a thing to look at, not a
                  refusal. */}
              {(trace.undeclared_keys?.length ?? 0) > 0 && (
                <Badge
                  variant="outline"
                  className="font-mono text-[11px] text-muted-foreground"
                  title={trace.undeclared_keys!.join(", ")}
                >
                  {trace.undeclared_keys!.length} undeclared key(s)
                </Badge>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {trace.ledger.length > 0 && <LedgerBox rows={trace.ledger} />}

      <div className="flex flex-col gap-3">
        {trace.stages.map((stage) => (
          <StageCard key={stage.stage} stage={stage.stage} fields={stage.fields} />
        ))}
      </div>
    </div>
  );
}

/** The governance ledger: one row per admitted execution attempt.
 *
 * Shown above the stage sections deliberately. It is the only part of the record that
 * says what the engine actually let reach the database, and `verdict_layer` names the
 * layer that refused when one did. */
function LedgerBox({ rows }: { rows: AuditLedgerRow[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Governance ledger — {rows.length} attempt(s)</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="flex flex-col gap-1">
          {rows.map((row, i) => (
            <li
              key={i}
              className="flex flex-wrap items-center gap-2 rounded bg-muted/50 px-2 py-1 font-mono text-[11px]"
            >
              {row.passed ? (
                <CheckCircle2 className="size-3.5 text-tier-governed" />
              ) : (
                <ShieldX className="size-3.5 text-tier-refused" />
              )}
              <span>{row.reason_code ?? "—"}</span>
              {row.verdict_layer && <span className="opacity-70">layer={row.verdict_layer}</span>}
              {row.path && <span className="opacity-70">path={row.path}</span>}
              {row.detail && <span className="opacity-70">{row.detail}</span>}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function StageCard({ stage, fields }: { stage: string; fields: AuditTraceField[] }) {
  const present = fields.filter((f) => f.present).length;
  const absent = fields.filter((f) => f.required_and_absent);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-mono text-sm">{stage}</CardTitle>
        <CardAction className="font-mono text-[11px] text-muted-foreground">
          {present}/{fields.length} present
          {absent.length > 0 && (
            <span className="ml-2 text-tier-refused">{absent.length} required absent</span>
          )}
        </CardAction>
      </CardHeader>
      <CardContent>
        <dl className="flex flex-col gap-1">
          {fields.map((field) => (
            <FieldRow key={field.name} field={field} />
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

function FieldRow({ field }: { field: AuditTraceField }) {
  return (
    <div
      className={cn(
        "grid grid-cols-[12rem_1fr] gap-3 rounded px-2 py-1",
        field.required_and_absent && "bg-tier-refused/5",
      )}
      title={field.why}
    >
      <dt
        className={cn(
          "font-mono text-[11px]",
          field.present ? "" : "text-muted-foreground",
          field.required_and_absent && "text-tier-refused",
        )}
      >
        {field.name}
      </dt>
      <dd className="min-w-0 font-mono text-[11px] break-words">
        {field.present ? (
          <FieldValue value={field.value} />
        ) : (
          <span className="text-muted-foreground">
            {field.required_and_absent ? "REQUIRED, ABSENT" : "not recorded"}
          </span>
        )}
      </dd>
    </div>
  );
}

/** Values are rendered as JSON, not prettified per type.
 *
 * A record field's value is whatever the register declares it to be, and inventing a
 * per-field renderer here would be this app's own opinion about a shape the engine
 * owns — the drift the zod boundary exists to prevent. Long values are clamped rather
 * than truncated in the string, so nothing is silently shortened. */
function FieldValue({ value }: { value: unknown }) {
  if (typeof value === "string") {
    return <span className="line-clamp-6 whitespace-pre-wrap">{value}</span>;
  }
  return (
    <span className="line-clamp-6 whitespace-pre-wrap">
      {JSON.stringify(value, null, value && typeof value === "object" ? 1 : 0)}
    </span>
  );
}

/* ── small shared pieces ──────────────────────────────────────────────────── */

function SectionTitle({ icon: Icon, title }: { icon: LucideIcon; title: string }) {
  return (
    <h2 className="flex items-center gap-2 text-sm font-semibold tracking-tight">
      <Icon className="size-4 text-muted-foreground" />
      {title}
    </h2>
  );
}
