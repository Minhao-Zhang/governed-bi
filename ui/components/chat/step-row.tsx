"use client";

import { createElement, useState } from "react";
import { AlertTriangle, Ban, ChevronRight, Loader2, XCircle } from "lucide-react";

import { SqlBlock } from "@/components/answer/sql-block";
import { stepCounts, stepIcon, type StepStatus, type TimelineStep } from "@/lib/steps";
import { cn } from "@/lib/utils";

/**
 * One row of the agent timeline: a status glyph, the step label, an `attempt`
 * badge on the governance rows that carry one, and an expandable detail. The row
 * IS the stage event — live, or replayed from a recorded ledger through the same
 * renderer, so the audit and the live view cannot disagree.
 *
 * **The disclosure is driven by one declaration, `FACT_KEYS`.** Its previous form
 * had three: `hasDetail`, `prefersOpenDetail` and a `switch` in `StepDetail`, each
 * listing step names separately. They agreed while the names were v1's and then
 * silently stopped agreeing — after the engine renamed its stages, `hasDetail`
 * matched nothing, so **no row was expandable at all** and the SQL, the block
 * reason and the counts were unreachable while all three functions still looked
 * correct. One table cannot drift from itself.
 */
export function StepRow({
  step,
  indent = false,
  defaultOpen = false,
}: {
  step: TimelineStep;
  indent?: boolean;
  /** Open the detail disclosure when the parent wants this row visible in-thread. */
  defaultOpen?: boolean;
}) {
  // `defaultOpen` is not an initial value — the timeline flips it as a step settles
  // so the detail drops into the main thread without a click — so it is *derived*
  // openness, with the user's own click pinned over the top of it. An effect that
  // pushed it into state instead would re-render on every settle, and could not
  // tell "the reader closed this" from "it was never open".
  const [pinned, setPinned] = useState<boolean | null>(null);
  const open = pinned ?? defaultOpen;
  const detail = step.detail ?? {};
  // `check` rows carry the repair loop's attempt number. The badge, not a fact
  // row: "attempt 2" beside the label is how a reader sees a repair happening.
  const attempt = typeof detail.attempt === "number" ? detail.attempt : null;
  const expandable = hasDetail(step);

  const label = (
    <span className="flex min-w-0 items-center gap-2">
      <StepGlyph step={step} />
      <span className={cn("truncate", step.status === "running" && "font-medium")}>{step.label}</span>
      {attempt !== null && (
        <span
          className={cn(
            "shrink-0 rounded-full border px-1.5 py-px text-[10px] font-medium tabular-nums",
            step.status === "blocked" || step.status === "error"
              ? "border-tier-fenced-raw/50 text-tier-fenced-raw"
              : "border-border text-muted-foreground",
          )}
        >
          attempt {attempt}
        </span>
      )}
    </span>
  );

  return (
    <li
      className={cn("text-sm", indent && "ml-6")}
      aria-current={step.status === "running" ? "step" : undefined}
    >
      {expandable ? (
        <button
          type="button"
          onClick={() => setPinned(!open)}
          aria-expanded={open}
          className="flex w-full items-center gap-1 rounded py-0.5 text-left hover:bg-muted/50"
        >
          <ChevronRight
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-90",
            )}
            aria-hidden
          />
          {label}
        </button>
      ) : (
        <div className="flex items-center gap-1 py-0.5 pl-[1.125rem]">{label}</div>
      )}

      {expandable && open && (
        <div className="ml-[1.125rem] mt-1 space-y-2 border-l pl-3 text-xs text-muted-foreground">
          <StepDetail step={step} />
        </div>
      )}
    </li>
  );
}

/* ── status glyph ─────────────────────────────────────────────────────────── */

/**
 * Status → icon, with the meaning carried by an `aria-label` and never by colour
 * alone.
 *
 * A settled-clean row shows **its own step icon** rather than a green tick: with
 * twenty-odd rows in a turn, a column of identical ticks is unreadable, and
 * `stepIcon` exists precisely so a rail and a tool can be told apart at a glance.
 * Anything that is not clean keeps a status glyph, because that is the one thing
 * a step icon cannot say.
 *
 * `miss` counts as clean: a gate that ran and matched nothing is the good case.
 * `hit` does not: the only gate that reports one is `negative_gate`, and a hit
 * declines the turn.
 */
function StepGlyph({ step }: { step: TimelineStep }) {
  const cls = "size-4 shrink-0";
  switch (step.status) {
    case "running":
      return <Loader2 className={cn(cls, "animate-spin")} aria-label="in progress" />;
    case "ok":
    case "miss":
      // `createElement`, not `<Icon/>`: the tag is chosen per step, and binding a
      // component to a local before rendering it is what `react-hooks`'
      // static-components rule (rightly) refuses.
      return createElement(stepIcon(step.step), {
        className: cn(cls, "text-tier-governed"),
        "aria-label": "done",
      });
    case "blocked":
    case "cap":
    case "hit":
      // Amber: a governance decision, not a failure. The label says which.
      return (
        <AlertTriangle
          className={cn(cls, "text-tier-fenced-raw")}
          aria-label={statusLabel(step.status)}
        />
      );
    case "declined":
      return <Ban className={cn(cls, "text-tier-refused")} aria-label="declined" />;
    default:
      return (
        <XCircle className={cn(cls, "text-tier-refused")} aria-label={statusLabel(step.status)} />
      );
  }
}

function statusLabel(status: StepStatus): string {
  switch (status) {
    case "blocked":
      return "blocked";
    case "cap":
      return "attempt limit reached";
    case "hit":
      return "matched";
    case "error":
      return "error";
    case "refused":
      return "refused";
    case "miss":
      return "no match";
    default:
      return status;
  }
}

/* ── the one declaration ──────────────────────────────────────────────────── */

/**
 * Which `detail` keys each step's disclosure shows, in reading order.
 *
 * **Only what the label did not already say.** `defaultLabel` in `lib/steps.ts`
 * spends the headline fact of almost every step — "Routed to sales, from 4
 * candidates", "Read 2 asset bodies", "Executed, 4 rows" — so a disclosure listing
 * `schemas`/`n_candidates` again would put a chevron on every one of twenty-five
 * rows and pay it back with the sentence the reader just read. A chevron has to
 * mean *there is more inside*. Steps absent from this map have nothing more; that
 * is most of them, and it is the right answer for a rail whose only interesting
 * outcome is that it finished.
 *
 * `check` is the one deliberate overlap. It is the row that gets quoted into bug
 * reports, and `layer` / `reason` as a record is easier to copy out of than the
 * same two values inside a sentence.
 *
 * The vocabulary is ADR 0010 §4's throughout: per-step, closed, and numbers — no
 * nested item bags, no driver text, no result rows.
 */
const FACT_KEYS = new Map<string, readonly string[]>([
  // On `ok`, `connect`'s label spends its words on the crossings; the declined one
  // already names the licensed count, so this is additive exactly where the row is
  // clean, which is the common case.
  ["connect", ["n_licensed"]],
  ["check", ["layer", "reason_code"]],
  // The digest is the point: it is what the audit ledger stores for this
  // statement, so an expanded `execute` row is where a reader confirms that the
  // live view and the record are describing the same SQL.
  ["execute", ["n_columns", "truncated", "sql_sha256"]],
  ["stamp", ["failed_stage"]],
]);

/*
 * Four steps were dropped from this table when `lib/steps.ts` was rewritten
 * against ADR 0010's corrected contract, and the reason is worth keeping: their
 * labels grew to carry the fact. `resolve` now reads "Pulled in 4 referenced
 * assets, 3 licensed"; `agent_core` "Finished reasoning, 2 attempts"; a degraded
 * facet "Terms: 2 hits (embedding channel not wired)"; a gate that failed open
 * "Input gate failed open: the question went through unchecked". A disclosure
 * repeating any of those would be a chevron promising more and delivering the
 * sentence above it — and the failed-open row in particular must be readable
 * *without* a click, which a label is and a disclosure is not.
 */

/**
 * Legal on every step, so appended rather than listed per row: `wrap.py` puts it
 * on any node that raised and the tools put it on any call that did. The tool
 * labels already name it; the rail labels do not, and a crashed rail with an
 * unnamed cause is the row this exists for.
 */
const CRASH_KEYS: readonly string[] = ["error_type"];

/** How each key reads. A key with no entry here falls back to itself, so a detail
 * key the engine adds tomorrow shows up under its wire name rather than vanishing. */
const FACT_LABELS = new Map<string, string>([
  ["n_licensed", "licensed tables"],
  ["layer", "layer"],
  ["reason_code", "reason"],
  ["n_columns", "columns"],
  ["truncated", "truncated"],
  ["sql_sha256", "statement digest"],
  ["failed_stage", "failed in"],
  ["error_type", "error"],
]);

/** Keys rendered as monospace — ids, layer names and digests, not counts. */
const MONO_KEYS = new Set(["layer", "reason_code", "sql_sha256", "failed_stage", "error_type"]);

type Fact = { key: string; label: string; value: string; mono: boolean };

function factKeys(step: string): readonly string[] {
  return [...(FACT_KEYS.get(step) ?? []), ...CRASH_KEYS];
}

/** One fact per present key. Absent keys are skipped — a fact we did not observe
 * must not become a zero (`register/stages.py`'s note on `by_failed_stage`). */
function factsOf(step: TimelineStep): Fact[] {
  const d = step.detail ?? {};
  const out: Fact[] = [];
  for (const key of factKeys(step.step)) {
    const value = formatFact(d[key]);
    if (value === null) continue;
    out.push({
      key,
      label: FACT_LABELS.get(key) ?? key,
      value,
      mono: MONO_KEYS.has(key),
    });
  }
  return out;
}

function formatFact(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : null;
  if (typeof value === "string") return value.length > 0 ? value : null;
  if (Array.isArray(value)) {
    const items = value.filter((v) => typeof v === "string" || typeof v === "number").map(String);
    return items.length > 0 ? items.join(", ") : null;
  }
  return null;
}

/** The clarification Q&A, which has its own shape rather than a fact list. */
function clarificationOf(step: TimelineStep) {
  if (step.step !== "ask_user") return null;
  const d = step.detail ?? {};
  const question = stepCounts.str(d, "question");
  const why = stepCounts.str(d, "why");
  const answer = stepCounts.str(d, "answer");
  const declined = d.declined === true;
  if (!question && !answer && !declined) return null;
  return { question, why, answer, declined };
}

/** The executed statement. Only `execute` carries one — a blocked statement never
 * reaches the database, and ADR 0010 §4 keeps the *executed* SQL, not the model's
 * argument, so this is the same string the audit ledger hashed. */
function sqlOf(step: TimelineStep): string | null {
  return step.step === "execute" ? stepCounts.str(step.detail ?? {}, "sql") : null;
}

export function hasDetail(step: TimelineStep): boolean {
  return (
    sqlOf(step) !== null || clarificationOf(step) !== null || factsOf(step).length > 0
  );
}

/**
 * The statuses that mean *something was decided or something broke* — a governance
 * block, an attempt cap, a refusal, a decline, a crash, or the `negative_gate`
 * matching a known-bad question.
 *
 * Exported because <AgentTimeline/> needs the same answer to decide which phase
 * may collapse. Two copies of this set is the drift that made the whole disclosure
 * unreachable once; one row's glyph disagreeing with its group's would be the
 * quieter version of it.
 */
const TROUBLE = new Set<StepStatus>(["blocked", "error", "refused", "declined", "cap", "hit"]);

export function isTrouble(status: StepStatus): boolean {
  return TROUBLE.has(status);
}

/**
 * Whether this row's detail should already be open. A turn emits twenty to thirty
 * rows, so auto-opening on `ok` would bury the two that matter; the decisions and
 * the failures open themselves, everything else is one click away.
 */
export function prefersOpenDetail(step: TimelineStep): boolean {
  // A `running` row has nothing settled to show, and that covers the *pending*
  // clarification deliberately: while the graph waits, <ClarificationPrompt/> is
  // on screen above the composer carrying the same question and reason, so opening
  // this row too would print it twice.
  if (!hasDetail(step) || step.status === "running") return false;
  // Once answered it is the one clean row that opens: what the user was asked and
  // what they said is the part of the trace they are in.
  if (step.step === "ask_user") return true;
  return isTrouble(step.status);
}

function StepDetail({ step }: { step: TimelineStep }) {
  const clarification = clarificationOf(step);
  const sql = sqlOf(step);
  const facts = factsOf(step);

  return (
    <>
      {clarification && (
        <>
          {clarification.question && (
            <p>
              <span className="font-medium text-foreground">Q:</span> {clarification.question}
            </p>
          )}
          {clarification.why && <p className="italic">{clarification.why}</p>}
          {clarification.declined ? (
            <p>
              <span className="font-medium text-tier-refused">Declined</span> — the turn failed
              closed.
            </p>
          ) : clarification.answer ? (
            <p>
              <span className="font-medium text-foreground">You answered:</span> “
              {clarification.answer}”
            </p>
          ) : (
            <p className="italic">Awaiting your answer…</p>
          )}
        </>
      )}

      {sql && <SqlBlock sql={sql} />}

      {facts.length > 0 && (
        <dl className="space-y-0.5">
          {facts.map((fact) => (
            <div key={fact.key} className="flex min-w-0 items-baseline gap-2">
              <dt className="shrink-0">{fact.label}</dt>
              <dd
                className={cn(
                  "min-w-0 truncate text-foreground",
                  fact.mono ? "font-mono" : "tabular-nums",
                )}
              >
                {fact.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}
