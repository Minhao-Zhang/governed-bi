"use client";

import { useState, type ReactNode } from "react";
import { AlertTriangle, Check, ChevronRight, Loader2, Search, XCircle } from "lucide-react";

import { isTrouble, prefersOpenDetail, StepRow } from "@/components/chat/step-row";
import { summarizeSteps, type StepStatus, type TimelineStep } from "@/lib/steps";
import { cn } from "@/lib/utils";

/**
 * The governed turn as it happens: a live, append-only timeline over the stage
 * event stream (ADR 0010).
 *
 * **Why this is grouped now.** v1 emitted a handful of rows per turn and a flat
 * list was the right shape for them. v2 emits twenty to thirty — thirteen rails,
 * five of which (the facets) arrive *concurrently*, plus a tool row per governed
 * action and a `check`/`execute` pair per SQL attempt. Flat, that is a wall that
 * scrolls the interesting row off the screen while the reader is still parsing the
 * boring ones. So the rails fold into the three phases a turn actually has, each
 * collapsing once it settles, and the five facets fold into one row because they
 * are one gesture and finish in a jumble.
 *
 * **What grouping is not allowed to do is hide a decision.** A phase holding a
 * blocked `check`, a refusal, a decline, an attempt cap or a `negative_gate` hit
 * opens itself; only clean phases collapse, and a collapsed header still carries
 * the worst status inside it, so closing one by hand loses the detail and never
 * the fact. The terminals — `refuse`, `decline`, `stamp` — are not groupable at
 * all and always render as their own row.
 *
 * Nothing is ever dropped: a step this file does not recognise renders at the top
 * level rather than being filed into a phase it might not belong to, because a new
 * stage name appearing as an unstyled row is recoverable and a new stage name
 * vanishing is not.
 */
export function AgentTimeline({
  steps,
  isRunning,
  title = "How the answer was reached",
  defaultExpanded = false,
  preferOpenDetails = true,
}: {
  steps: TimelineStep[];
  isRunning: boolean;
  title?: string;
  /** Start the completed trace open (answer card). Provenance sheet leaves this false. */
  defaultExpanded?: boolean;
  /** Auto-open transparency step dropdowns. Off in the provenance sheet to stay compact. */
  preferOpenDetails?: boolean;
}) {
  // Live runs stay open; the answer card can opt into starting expanded so the
  // in-thread dropdowns survive after the placeholder is replaced by AnswerCard.
  const [open, setOpen] = useState(isRunning || defaultExpanded);

  const entries = groupSteps(steps);

  // Whether any step is mid-flight right now (its own row shows a spinner). When
  // the run is live but every emitted step has settled, the agent is deciding its
  // next move with nothing on screen to show it — so tack on a "working" row.
  const showWorking = isRunning && !steps.some((s) => s.status === "running");

  if (!isRunning && !open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded={false}
        className="flex items-center gap-1.5 rounded py-0.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronRight className="size-3.5 shrink-0" aria-hidden />
        <Check className="size-4 shrink-0 text-tier-governed" aria-hidden />
        {summarizeSteps(steps)}
      </button>
    );
  }

  return (
    <div className="space-y-2">
      {!isRunning && (
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-expanded
          className="flex items-center gap-1.5 rounded py-0.5 text-sm font-medium hover:text-foreground"
        >
          <ChevronRight className="size-3.5 shrink-0 rotate-90 text-muted-foreground" aria-hidden />
          {summarizeSteps(steps)}
        </button>
      )}

      {isRunning && <p className="text-xs font-medium text-muted-foreground">{title}</p>}

      <ol className="flex flex-col gap-0.5" aria-label={title} aria-live="polite">
        {entries.map((entry) =>
          entry.kind === "step" ? (
            <StepRow
              key={entry.step.key}
              step={entry.step}
              defaultOpen={preferOpenDetails && prefersOpenDetail(entry.step)}
            />
          ) : (
            <Phase
              key={entry.id}
              label={entry.title}
              steps={entry.steps}
              preferOpenDetails={preferOpenDetails}
            />
          ),
        )}

        {showWorking && (
          <li className="text-sm" aria-label="Working on the next step">
            <div className="flex items-center gap-2 py-1 pl-[1.125rem]">
              <WorkingDots />
            </div>
          </li>
        )}
      </ol>
    </div>
  );
}

/* ── phases ───────────────────────────────────────────────────────────────── */

/**
 * Which phase each rail belongs to. Tools are not listed: every `kind: "tool"`
 * row belongs to the reasoning phase by construction, since the tools *are* the
 * agent loop, and keying them off `kind` rather than a name list means a tool the
 * engine adds tomorrow lands in the right place without a change here.
 *
 * `refuse`, `decline` and `stamp` are deliberately absent. They are terminals —
 * one row, at the end, at the top level — and burying a refusal inside a
 * collapsible group is the one thing this component must not do.
 *
 * `abstain`, `reflect` and `narrate` are absent for the neighbouring reason. `abstain`
 * decides whether the turn answers at all (ADR 0013), and the other two run *after*
 * `agent_core`, so filing them under "reasoning" would fold two post-loop rows into the
 * group they follow. All three stay at top level, which is also what an unrecognised step
 * gets — `phaseOf` returns null and `groupSteps` passes the row through, so a stage from a
 * newer server degrades into a plain row rather than a wrong group or a crash.
 */
const PHASE_OF = new Map<string, string>([
  ["accept", "intake"],
  ["guard", "intake"],
  ["rewrite", "intake"],
  ["negative_gate", "intake"],
  ["facet_schema", "context"],
  ["facet_term", "context"],
  ["facet_metric", "context"],
  ["facet_entity", "context"],
  ["facet_example", "context"],
  ["route", "context"],
  ["resolve", "context"],
  ["connect", "context"],
  ["assemble", "context"],
  ["agent_core", "reasoning"],
]);

const PHASE_TITLES = new Map<string, string>([
  ["intake", "Reading the question"],
  ["context", "Finding the governed context"],
  ["reasoning", "Reasoning"],
]);

function phaseOf(step: TimelineStep): string | null {
  if (step.kind === "tool") return "reasoning";
  return PHASE_OF.get(step.step) ?? null;
}

type Entry =
  | { kind: "step"; step: TimelineStep }
  | { kind: "phase"; id: string; phase: string; title: string; steps: TimelineStep[] };

/**
 * Fold the ordered rows into phase groups, preserving order. Consecutive rows of
 * one phase merge; anything unphased passes through as its own entry. A phase
 * interrupted by an unphased row therefore appears twice, which is the honest
 * rendering of an order this file did not expect.
 */
function groupSteps(steps: TimelineStep[]): Entry[] {
  const entries: Entry[] = [];
  let n = 0;
  for (const step of steps) {
    const phase = phaseOf(step);
    if (phase === null) {
      entries.push({ kind: "step", step });
      continue;
    }
    const last = entries[entries.length - 1];
    if (last?.kind === "phase" && last.phase === phase) {
      last.steps.push(step);
      continue;
    }
    n += 1;
    entries.push({
      kind: "phase",
      id: `${phase}:${n}`,
      phase,
      title: PHASE_TITLES.get(phase) ?? phase,
      steps: [step],
    });
  }
  return entries;
}

/**
 * The two severities a settled group can carry. `failed` is a turn that stopped —
 * a crash or a refusal; `warn` is governance doing its job — a blocked statement,
 * an attempt cap, a decline, a known-bad match. Keeping them apart is the same
 * distinction `register/stages.py` draws between a crash and a refusal, and for
 * the same reason: one is a bug in us, the other is the product working.
 */
const FAILED = new Set<StepStatus>(["error", "refused"]);

type Health = "running" | "failed" | "warn" | "done";

function healthOf(steps: TimelineStep[]): Health {
  if (steps.some((s) => s.status === "running")) return "running";
  if (steps.some((s) => FAILED.has(s.status))) return "failed";
  if (steps.some((s) => isTrouble(s.status))) return "warn";
  return "done";
}

/**
 * One collapsible phase. `auto` is the derived openness — live or troubled phases
 * are open, settled clean ones are closed — and a click pins the user's choice
 * over it for the rest of the render's life. Pinning rather than re-deriving is
 * the point: a reader who opened a finished phase to read it should not have the
 * next event close it under them.
 */
function Phase({
  label,
  steps,
  preferOpenDetails,
}: {
  label: string;
  steps: TimelineStep[];
  preferOpenDetails: boolean;
}) {
  const [pinned, setPinned] = useState<boolean | null>(null);
  const health = healthOf(steps);
  const auto = health !== "done";
  const open = pinned ?? auto;
  const rows = mergeFacets(steps, preferOpenDetails);

  return (
    <li>
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
        <span className="flex min-w-0 items-center gap-2 text-sm">
          <HealthGlyph health={health} />
          <span className={cn("truncate", health === "running" && "font-medium")}>{label}</span>
          {!open && <CollapsedNote steps={steps} health={health} />}
        </span>
      </button>
      {open && <ol className="mt-0.5 flex flex-col gap-0.5">{rows}</ol>}
    </li>
  );
}

/**
 * What a collapsed phase says about itself.
 *
 * A clean phase says how many steps it holds, because there is nothing else to
 * report. A phase that decided something says **the decision**, borrowing the
 * worst row's own label — so "Reading the question · 4 steps" becomes "Reading the
 * question · Input gate failed open: the question went through unchecked". The
 * requirement is that grouping never hides a decision; a step count is exactly the
 * summary that would have hidden one, and this is a phase a user may legitimately
 * have clicked shut.
 */
function CollapsedNote({ steps, health }: { steps: TimelineStep[]; health: Health }) {
  const worst =
    health === "failed"
      ? steps.find((s) => FAILED.has(s.status))
      : health === "warn"
        ? steps.find((s) => isTrouble(s.status))
        : undefined;
  return (
    <span
      className={cn(
        "min-w-0 truncate text-xs",
        worst ? "text-tier-fenced-raw" : "text-muted-foreground tabular-nums",
      )}
    >
      {worst ? worst.label : `${steps.length} step${steps.length === 1 ? "" : "s"}`}
    </span>
  );
}

/**
 * Render a phase's rows, with the five facets folded into one.
 *
 * They fan out concurrently and resolve in whatever order the executor finishes
 * them, so as five rows they read as flicker. As one row they read as what they
 * are: one retrieval pass with a hit count.
 */
function mergeFacets(steps: TimelineStep[], preferOpenDetails: boolean): ReactNode[] {
  const facets = steps.filter((s) => s.step.startsWith("facet_"));
  const out: ReactNode[] = [];
  let placedFacets = false;

  for (const step of steps) {
    if (step.step.startsWith("facet_")) {
      // In the position the first facet took, so the group keeps its place in the
      // order rather than jumping to the top or bottom of the phase.
      if (placedFacets) continue;
      placedFacets = true;
      out.push(
        <FacetGroup key="facets" facets={facets} preferOpenDetails={preferOpenDetails} />,
      );
      continue;
    }
    out.push(
      <StepRow
        key={step.key}
        step={step}
        indent
        defaultOpen={preferOpenDetails && prefersOpenDetail(step)}
      />,
    );
  }
  return out;
}

function FacetGroup({
  facets,
  preferOpenDetails,
}: {
  facets: TimelineStep[];
  preferOpenDetails: boolean;
}) {
  const health = healthOf(facets);
  const [pinned, setPinned] = useState<boolean | null>(null);
  // Only trouble opens this one automatically. A facet fan-out that is merely
  // still running is exactly the case the merge exists for.
  const open = pinned ?? (health === "failed" || health === "warn");

  const settled = facets.filter((s) => s.status !== "running").length;
  const hits = facets.reduce((sum, s) => {
    const n = s.detail?.n_hits;
    return typeof n === "number" ? sum + n : sum;
  }, 0);
  // A facet resolves `error` when a retrieval channel was not wired up: retrieval
  // was *degraded*, not empty. Summing hits across five facets and reporting the
  // total is precisely how that disappears — the four that worked carry the number
  // and the one that never looked contributes a silent zero, which is
  // `_channels_for`'s own defect (ADR 0010, adversarial finding 4) reproduced in a
  // summary line. So the count of degraded facets rides beside the total, and this
  // is also why an errored fan-out opens itself: the channel names are on the rows.
  const degraded = facets.filter((s) => s.status === "error").length;

  const summary =
    health === "running"
      ? `${settled} of ${facets.length} done`
      : `${facets.length} facets · ${hits} hit${hits === 1 ? "" : "s"}`;

  return (
    <li className="ml-6 text-sm">
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
        <span className="flex min-w-0 items-center gap-2">
          {health === "running" ? (
            <Loader2 className="size-4 shrink-0 animate-spin" aria-label="in progress" />
          ) : health === "done" ? (
            <Search className="size-4 shrink-0 text-tier-governed" aria-label="done" />
          ) : (
            <HealthGlyph health={health} />
          )}
          <span className={cn("truncate", health === "running" && "font-medium")}>
            Retrieving governed assets
          </span>
          <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{summary}</span>
          {degraded > 0 && (
            <span className="shrink-0 text-xs font-medium text-tier-fenced-raw tabular-nums">
              {degraded} degraded
            </span>
          )}
        </span>
      </button>
      {open && (
        <ol className="mt-0.5 flex flex-col gap-0.5">
          {facets.map((facet) => (
            <StepRow
              key={facet.key}
              step={facet}
              indent
              defaultOpen={preferOpenDetails && prefersOpenDetail(facet)}
            />
          ))}
        </ol>
      )}
    </li>
  );
}

function HealthGlyph({ health }: { health: Health }) {
  const cls = "size-4 shrink-0";
  switch (health) {
    case "running":
      return <Loader2 className={cn(cls, "animate-spin")} aria-label="in progress" />;
    case "failed":
      return <XCircle className={cn(cls, "text-tier-refused")} aria-label="failed" />;
    case "warn":
      return (
        <AlertTriangle className={cn(cls, "text-tier-fenced-raw")} aria-label="needs attention" />
      );
    default:
      return <Check className={cn(cls, "text-tier-governed")} aria-label="done" />;
  }
}

/** Three dots that bounce in sequence — a live "still working" affordance shown
 * between settled steps while the agent decides its next move. */
function WorkingDots() {
  return (
    <span className="flex items-center gap-1" aria-hidden>
      <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.3s]" />
      <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.15s]" />
      <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60" />
    </span>
  );
}
