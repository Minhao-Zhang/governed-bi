/**
 * The step model: wire stage events → UI timeline rows.
 *
 * The serve graph emits one custom event per governed boundary — every rail from
 * `serve/wrap.py`, every tool and every governance verdict from `serve/tools.py`,
 * and the terminal from `serve/nodes/stamp.py`. ADR 0010 is the spec for that
 * event; this module owns the client half of it:
 *
 *  - `reduceSteps` folds the ordered stream into append-only rows, merging a
 *    step's `start` and its resolution into one row on the shared `id`.
 *  - `buildStepsFromLedger` maps a completed recorded ledger to the SAME rows, so
 *    the post-answer audit reuses the live renderer over one data shape.
 *
 * **The vocabulary is `register/stages.py`, not this file** (ADR 0010 §2). Two
 * consequences that look like omissions and are not:
 *
 *  - There is no `run_query`. A SQL call emits `check` (the governance verdict)
 *    and then `execute` (what actually reached the database); a third row would
 *    double-count an action the ledger and every rate already agree on. So a
 *    *repair* is a `check` row that did not pass — see `countRepairs` — and a
 *    *query* is an `execute` row.
 *  - `execute` and `cap` never emit `start`, because their status is read off a
 *    completed record rather than declared on entry (ADR 0010 §3). Nothing here
 *    may assume a row passes through `running`.
 *
 * v1's `refuse_gate`, `cache`, `schema_route`, `finalize`, `search_corpus`,
 * `read_notes`, `grep_notes` and `run_query` name concepts this engine does not
 * have, so they are gone rather than kept as fallbacks: a name this module still
 * answers to is a name a reader will believe the engine can produce.
 *
 * The step view is driven by these custom events only — never by the agent's
 * internal chat messages (ADR 0001 / gotcha G2), which stay node-local.
 */

import {
  Ban,
  BookOpenText,
  Bot,
  CircleSlash,
  Gavel,
  Hand,
  Inbox,
  Layers,
  Link2,
  MessageCircleQuestionMark,
  Network,
  PenLine,
  Play,
  Rows3,
  Search,
  ShieldCheck,
  ShieldX,
  Sparkles,
  Stamp,
  Table2,
  Waypoints,
  type LucideIcon,
} from "lucide-react";

/** The custom stream event (backend contract, ADR 0010). start/resolve share `id`. */
export interface GovEvent {
  /**
   * Monotonic within the emitting process, and that is the whole of it: it
   * disambiguates order *within* one stream. **Cross-stream position is the
   * client's** — a clarification resumes on a second connection whose counter may
   * have restarted, and the engine cannot supply a number that survives a restart,
   * so `reduceSteps` orders by arrival and never treats this as a global ordinal.
   */
  seq: number;
  id?: string; // stable per logical step; start + resolve share it
  kind: "rail" | "tool" | "final";
  /**
   * A `register/stages.py` Stage value: rails
   * accept|guard|rewrite|negative_gate|facet_{schema,term,metric,entity,example}|
   * route|resolve|connect|assemble|agent_core|refuse|decline, tools
   * read_body|inspect_schema|sample_rows|check|execute|cap|ask_user, final stamp.
   */
  step: string;
  status: "start" | "ok" | "blocked" | "error" | "refused" | "cap" | "hit" | "miss" | "declined";
  label?: string;
  /**
   * Per-step and closed-vocabulary only (ADR 0010 §4): turn_index, rule_id, gate,
   * rewritten, asset_id, n_hits, failed_channels, schemas, n_candidates,
   * n_pulled_in, n_licensed, n_crossings, `reason` and `terminal_reason` (the same
   * string under both names, deliberately), n_chars, n_attempts, n_asset_ids,
   * table_id, column_id, limit, attempt, layer, reason_code, sql, sql_sha256,
   * row_count, truncated, n_columns, cap, clarification_id, outcome, failed_stage,
   * error_type. There is no `n_assets`: it was declared and never emitted, and the
   * contract deleted it rather than invent an unobservable count.
   * Every key is optional on the wire — labels must degrade without it.
   */
  detail?: Record<string, unknown>;
  serve_path?: "agent"; // present on the first event of a turn (only the agent core serves now)
}

/** The resolved statuses a row can settle into (everything except `start`). */
export type StepStatus =
  | "running"
  | "ok"
  | "blocked"
  | "error"
  | "refused"
  | "cap"
  | "hit"
  | "miss"
  | "declined";

/** The UI row (a step's start + resolve merged into one row). */
export interface TimelineStep {
  key: string;
  seq: number;
  kind: GovEvent["kind"];
  step: string;
  status: StepStatus;
  label: string;
  detail: Record<string, unknown>;
}

/**
 * The steps that run inside the agent loop. Must agree with the `kind` the wire
 * sends — it is the authority; this set exists for the ledger path, where no
 * `kind` was recorded.
 *
 * `check` and `cap` are here because they are governance decisions *about* a tool
 * call, emitted from the same boundary (`serve/tools.py`) and rendered in the
 * same indent as the call they judge.
 *
 * `cap` is a tool even though `register/stages.py` files it under
 * `TERMINAL_STAGES` beside the `refuse`/`decline` rails, because the two facts
 * live at different levels: the `cap` **row** says *this tool call was refused a
 * slot*, keyed on its `tool_call_id`, while the **turn** being cap-terminated is a
 * separate `stamp` row with `status: "cap"`. A capped turn emits both and they are
 * not duplicates.
 */
const TOOL_STEPS = new Set([
  "read_body",
  "inspect_schema",
  "sample_rows",
  "check",
  "execute",
  "cap",
  "ask_user", // HITL clarification is a governed tool call (ADR 0010)
]);

/** The one step whose `kind` is `final`: every terminal path funnels through it. */
const FINAL_STEP = "stamp";

/**
 * The five concurrent facets → the noun their row is about. A `Map`, not an
 * object literal: `step` is an unvalidated wire string, and an object lookup
 * answers for `constructor` and every other `Object.prototype` key.
 */
const FACET_NOUNS = new Map<string, string>([
  ["facet_schema", "Schema"],
  ["facet_term", "Terms"],
  ["facet_metric", "Metrics"],
  ["facet_entity", "Entities"],
  ["facet_example", "Examples"],
]);

export function isTool(step: string): boolean {
  return TOOL_STEPS.has(step);
}

/**
 * Everything the agent loop does not do. Note `stamp` answers `true` here while
 * its wire `kind` is `final` — when a caller holds a row, prefer `row.kind`.
 */
export function isRail(step: string): boolean {
  return !isTool(step);
}

/** Icon per step, so rails and each tool read distinctly at a glance. */
export function stepIcon(step: string): LucideIcon {
  if (FACET_NOUNS.has(step)) return Search; // the five facets are one gesture
  switch (step) {
    case "accept":
      return Inbox;
    case "guard":
      return ShieldCheck;
    case "rewrite":
      return PenLine;
    case "negative_gate":
      return Ban;
    case "route":
      return Waypoints;
    case "resolve":
      return Link2;
    case "connect":
      return Network;
    case "assemble":
      return Layers;
    case "agent_core":
      return Bot;
    case "read_body":
      return BookOpenText;
    case "inspect_schema":
      return Table2;
    case "sample_rows":
      return Rows3;
    case "check":
      return Gavel;
    case "execute":
      return Play;
    case "cap":
      return CircleSlash;
    case "ask_user":
      return MessageCircleQuestionMark;
    case "refuse":
      return ShieldX;
    case "decline":
      return Hand;
    case FINAL_STEP:
      return Stamp;
    default:
      return Sparkles;
  }
}

function num(detail: Record<string, unknown> | undefined, key: string): number | null {
  const v = detail?.[key];
  return typeof v === "number" ? v : null;
}

function str(detail: Record<string, unknown> | undefined, key: string): string | null {
  const v = detail?.[key];
  return typeof v === "string" && v.length > 0 ? v : null;
}

function strList(detail: Record<string, unknown> | undefined, key: string): string[] {
  const v = detail?.[key];
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === "string" && x.length > 0);
}

/** "1 hit" / "3 hits"; pass `many` for the nouns English does not pluralise with -s. */
function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * "Metrics: 3 hits" — one wording for all five facets, which arrive interleaved.
 *
 * The `error` status here is **not** "something broke". It means a declared
 * retrieval channel never ran, which on a deployment with no embedder configured
 * is every facet on every turn: `semantic` is declared and never consulted. So the
 * copy names the missing channel and keeps the hits, because both facts are real —
 * a facet can find 17 things through the channel that did work while two others
 * never ran. Two things this must not do: report it as `0 hits` (the old
 * behaviour — "the corpus has nothing to say" instead of "we never looked"), or
 * read like a fault, which on a row that fires 100% of the time would teach the
 * reader to ignore every red row including the ones that are faults.
 */
function facetLabel(
  noun: string,
  running: boolean,
  status: string,
  detail: Record<string, unknown> | undefined,
): string {
  if (running) return `Retrieving ${noun.toLowerCase()}`;
  const n = num(detail, "n_hits");
  const hits = n === null ? null : plural(n, "hit");
  if (status === "error") {
    const failed = strList(detail, "failed_channels");
    if (failed.length === 0) return `${noun}: retrieval failed`;
    const channels = `${failed.join(" + ")} ${failed.length === 1 ? "channel" : "channels"} not wired`;
    return hits === null ? `${noun}: ${channels}` : `${noun}: ${hits} (${channels})`;
  }
  return hits === null ? `${noun} retrieved` : `${noun}: ${hits}`;
}

/** `stamp`'s `outcome` → the one line a reader wants (register/stages.py Outcome). */
function outcomeLabel(outcome: string, failedStage: string | null): string {
  switch (outcome) {
    case "answered":
      return "Answered";
    case "refused":
      return "Refused";
    case "clarification":
      return "Waiting on your answer";
    case "capped":
      return "Stopped at the attempt limit";
    case "crashed":
      return failedStage ? `Failed in ${failedStage}` : "Failed";
    // Not "Answered": the turn ended without running a governed statement, and the `stamp` row
    // carries `status: "ok"` because the rail's status vocabulary has no word for this. The label
    // is where the distinction lands, so it has to say it.
    case "no_sql":
      return "Answered without running a query";
    default:
      return outcome;
  }
}

/**
 * A short human label when the backend omits `label` — which is always, since
 * the wire deliberately leaves copy to the client (ADR 0010). Status-dependent
 * by design: "Input gate: refused" and "Input gate: cleared" are the same row.
 */
export function defaultLabel(ev: {
  step: string;
  status: GovEvent["status"] | StepStatus;
  detail?: Record<string, unknown>;
}): string {
  const d = ev.detail;
  const s = ev.status;
  const running = s === "start" || s === "running";
  const facet = FACET_NOUNS.get(ev.step);
  if (facet !== undefined) return facetLabel(facet, running, s, d);

  switch (ev.step) {
    /* ── rails ─────────────────────────────────────────────────────────────── */
    case "accept": {
      if (running) return "Accepting the turn";
      if (s === "error") return "Could not start the turn";
      const i = num(d, "turn_index");
      return i === null ? "Accepted the turn" : `Accepted turn ${i}`;
    }
    case "guard": {
      if (running) return "Checking the input gate";
      if (s === "blocked") {
        const rule = str(d, "rule_id");
        return rule ? `Input gate: refused (${rule})` : "Input gate: refused";
      }
      if (s === "error") {
        return str(d, "gate") === "error_failed_open"
          ? failedOpen("Input gate")
          : "Input gate: failed";
      }
      return "Input gate: cleared";
    }
    case "rewrite": {
      if (running) return "Rewriting the question";
      // A failed rewrite used to report `rewritten: true`, because the flag was
      // derived as `outcome != "unchanged"`. It now arrives as `error` with the
      // flag false, and the turn continues with the question as asked — which is
      // the part a reader needs, since the answer is about the original wording.
      if (s === "error") return "Rewrite failed, kept the question as asked";
      return d?.rewritten === true ? "Rewrote the follow-up" : "No rewrite needed";
    }
    case "negative_gate": {
      if (running) return "Checking known-bad questions";
      if (s === "error") {
        return str(d, "gate") === "error_failed_open"
          ? failedOpen("Negative gate")
          : errorLabel("Negative gate failed", d);
      }
      if (s === "hit") {
        const asset = str(d, "asset_id");
        return asset
          ? `Matched a known-bad question (${asset})`
          : "Matched a known-bad question";
      }
      // `ok` with `gate: "disabled"` is the only status this step emits today:
      // `negative_tau` is unset until a negative corpus exists, and the emitter
      // will not report a disabled gate as `miss` — that would claim it looked
      // and found nothing. So "No negative match" is reserved for a `miss`, when
      // the gate really did run. Getting these two the same way round is the
      // difference between a screen that says what happened and one that lies on
      // every turn.
      if (str(d, "gate") === "disabled") return "Negative-example gate: disabled";
      return s === "ok" ? "Negative gate: cleared" : "No negative match";
    }
    case "route": {
      if (running) return "Selecting a schema";
      if (s === "error") return "Routing failed";
      const n = num(d, "n_candidates");
      if (s === "declined") {
        // A declining node keeps the numbers it did get as far as computing, and
        // the row that most needs explaining used to be the least informative one.
        // `no_schema_matched` is the canonical reason and this copy already says
        // it, so echoing it would read "No schema matched: no_schema_matched";
        // any other reason is shown raw rather than swallowed.
        const why = reasonOf(d);
        const head =
          why && why !== "no_schema_matched" ? `Routing declined: ${why}` : "No schema matched";
        return n === null ? head : `${head}, from ${n} candidates`;
      }
      const picked = strList(d, "schemas").join(", ");
      if (picked && n !== null) return `Routed to ${picked}, from ${n} candidates`;
      if (picked) return `Routed to ${picked}`;
      return "Selected a schema";
    }
    case "resolve": {
      if (running) return "Resolving references";
      if (s === "error") return "Reference resolution failed";
      const pulled = num(d, "n_pulled_in");
      const licensed = num(d, "n_licensed");
      const head =
        pulled === null
          ? "Resolved references"
          : pulled === 0
            ? "No references to pull in"
            : `Pulled in ${plural(pulled, "referenced asset")}`;
      return licensed === null ? head : `${head}, ${licensed} licensed`;
    }
    case "connect": {
      if (running) return "Finding a join path";
      if (s === "error") return "Join planning failed";
      const crossings = num(d, "n_crossings");
      if (s === "declined") {
        const why = reasonOf(d);
        const head = why ? (CONNECT_DECLINES.get(why) ?? `Cannot connect: ${why}`) : "Cannot connect";
        const licensed = num(d, "n_licensed");
        const bits: string[] = [];
        if (licensed !== null) bits.push(plural(licensed, "table licensed", "tables licensed"));
        if (crossings !== null) bits.push(plural(crossings, "crossing"));
        return bits.length > 0 ? `${head} (${bits.join(", ")})` : head;
      }
      if (crossings === null) return "Connected the tables";
      return crossings === 0
        ? "Connected within one schema"
        : `Connected across ${plural(crossings, "schema boundary", "schema boundaries")}`;
    }
    case "assemble": {
      if (running) return "Assembling governed context";
      if (s === "error") return "Context assembly failed";
      // `n_chars` and nothing else. `n_assets` was declared in the contract and
      // never emitted, and the fix was to delete it rather than produce it: the
      // node returns only its delivery, so a count would mean reading state the
      // node did not return — one reader consulting something other than the
      // update, for one cosmetic number. An absent fact stays absent.
      const chars = num(d, "n_chars");
      return chars === null
        ? "Assembled governed context"
        : `Assembled governed context (${chars} chars)`;
    }
    case "agent_core": {
      if (running) return "Reasoning over the governed context";
      const attempts = num(d, "n_attempts");
      if (s === "error") {
        return attempts === null
          ? "The agent run failed"
          : `The agent run failed after ${plural(attempts, "attempt")}`;
      }
      return attempts === null
        ? "Finished reasoning"
        : `Finished reasoning, ${plural(attempts, "attempt")}`;
    }
    case "refuse": {
      if (running) return "Refusing";
      if (s === "error") return "The refusal path failed";
      const why = reasonOf(d);
      return why ? `Refused: ${why}` : "Refused";
    }
    case "decline": {
      if (running) return "Declining";
      if (s === "error") return "The decline path failed";
      const why = reasonOf(d);
      return why ? `Declined: ${why}` : "Declined";
    }

    /* ── tools ─────────────────────────────────────────────────────────────── */
    case "read_body": {
      if (running) return "Reading asset bodies";
      if (s === "blocked") return outOfScope("those assets", true);
      if (s === "error") return errorLabel("Could not read asset bodies", d);
      const n = num(d, "n_asset_ids");
      return n === null ? "Read asset bodies" : `Read ${plural(n, "asset body", "asset bodies")}`;
    }
    case "inspect_schema": {
      const table = str(d, "table_id");
      if (running) return table ? `Inspecting ${table}` : "Inspecting schema";
      if (s === "blocked") return outOfScope(table);
      if (s === "error") return errorLabel("Inspection failed", d);
      return table ? `Inspected ${table}` : "Inspected schema";
    }
    case "sample_rows": {
      const column = str(d, "column_id");
      if (running) return column ? `Sampling ${column}` : "Sampling rows";
      if (s === "blocked") return outOfScope(column);
      if (s === "error") return errorLabel("Sampling failed", d);
      const limit = num(d, "limit");
      if (column) return limit === null ? `Sampled ${column}` : `Sampled ${column}, ${limit} rows`;
      return "Sampled rows";
    }
    case "check": {
      if (running) return "Checking against governance";
      if (s === "error") return "The governance check failed";
      if (s === "blocked") {
        const reason = str(d, "reason_code");
        const layer = str(d, "layer");
        if (reason && layer) return `Governance blocked: ${reason} (${layer})`;
        if (reason) return `Governance blocked: ${reason}`;
        return layer ? `Governance blocked at the ${layer} layer` : "Governance blocked";
      }
      return "Governance cleared";
    }
    case "execute": {
      if (s === "error") return "Execution failed";
      const rows = num(d, "row_count");
      if (rows === null) return "Executed";
      return d?.truncated === true
        ? `Executed, ${plural(rows, "row")} (truncated)`
        : `Executed, ${plural(rows, "row")}`;
    }
    case "cap": {
      const cap = num(d, "cap");
      return cap === null ? "Attempt limit reached" : `Attempt limit reached (${cap})`;
    }
    case "ask_user":
      if (s === "declined") return "Question declined";
      if (s === "ok") return "Question answered";
      return "Asked a question";

    /* ── terminal ──────────────────────────────────────────────────────────── */
    case FINAL_STEP: {
      const failed = str(d, "failed_stage");
      const outcome = str(d, "outcome");
      // For this one status the wire is *more* specific than the outcome and wins:
      // `Outcome` has no `declined` member, so a decline classifies as `refused` —
      // right for measurement, wrong for a timeline, where "no schema matched" and
      // "the guard blocked this" are not the same event to the person reading it.
      // `clarification` still wins, because waiting on an answer is a state rather
      // than a failure (ADR 0010, `stamp` reads `path_kind` first for exactly this).
      if (s === "declined" && outcome !== "clarification") return "Declined";
      if (outcome) return outcomeLabel(outcome, failed);
      if (s === "refused") return "Refused";
      if (s === "cap") return "Stopped at the attempt limit";
      if (s === "error") return failed ? `Failed in ${failed}` : "Failed";
      return running ? "Finishing the turn" : "Answered";
    }
    default:
      // An unrecognised step is renderable-but-unlabelled, never an error: `Stage`
      // declares members this stream does not emit yet (`graded_delivery`,
      // `repair`) and the client must not break when one starts arriving.
      return ev.step;
  }
}

/** Tool errors carry `error_type` only — never the driver's text (ADR 0010 §4). */
function errorLabel(prefix: string, detail: Record<string, unknown> | undefined): string {
  const type = str(detail, "error_type");
  return type ? `${prefix} (${type})` : prefix;
}

/**
 * The refusal/decline reason, read under both of its names.
 *
 * The engine emits the same string twice on purpose: `reason` is what ADR 0010's
 * table declared and what this module renders, `terminal_reason` is what the state
 * channel and the audit record call it, so a reader comparing a row against the
 * record finds the key it expects. The two names had drifted apart by one word,
 * and the cost was that the single most important row on a failed turn — the
 * decline — rendered with no explanation at all. Reading both means neither side
 * can break it again by settling on the other name.
 */
function reasonOf(detail: Record<string, unknown> | undefined): string | null {
  return str(detail, "reason") ?? str(detail, "terminal_reason");
}

/**
 * Governance refused a read: the id was outside this turn's bounds.
 *
 * Distinct from `error`, which means *we* are broken — no connector, a raising
 * driver. The read-only tools do not throw when bounds say no, they return the
 * out-of-scope message, so a refusal used to resolve `ok` and the timeline read
 * "Inspected sales.audit_log" for a table the turn was refused. A bounds refusal
 * writes no ledger attempt and no delivery entry, which makes this row the only
 * record anywhere that it happened.
 */
function outOfScope(subject: string | null, plural = false): string {
  if (subject === null) return "Refused: not in scope for this turn";
  return `Refused: ${subject} ${plural ? "are" : "is"} not in scope for this turn`;
}

/**
 * A gate ran, one of its rules threw, and the question went through **unchecked**.
 * The record counts this as a security event and gates a run on it, so this row
 * must not read like a clean pass — it is the one row where alarming the reader is
 * the correct behaviour.
 */
function failedOpen(gate: string): string {
  return `${gate} failed open: the question went through unchecked`;
}

/**
 * `connect`'s two declared decline reasons, in prose. Mapped rather than echoed
 * because "No join path: missing_join_path" says it twice, and because the two are
 * opposite situations: no path at all, versus a path that blew the bound. An
 * unrecognised reason is echoed raw, so a new one is never silently swallowed.
 */
const CONNECT_DECLINES = new Map<string, string>([
  ["missing_join_path", "No join path"],
  ["over_connect_bounds", "Join path over the bound"],
]);

/**
 * One past the last position held, or the event's own `seq` for the very first row.
 *
 * Reduced rather than read off the end so this does not depend on `prev` being
 * sorted, and reduced rather than spread into `Math.max` so it does not depend on
 * the argument limit either.
 */
function nextPosition(prev: TimelineStep[], first: number): number {
  if (prev.length === 0) return first;
  return prev.reduce((max, s) => (s.seq > max ? s.seq : max), prev[0].seq) + 1;
}

/**
 * Fold one event into the accumulated rows. A step's `start` and its resolution
 * share `id`, so they merge into a single row (status advances `running` → the
 * terminal status, detail deep-merges). Rows stay in the order they were first
 * seen, which is what lets the five concurrent facets arrive interleaved.
 */
export function reduceSteps(prev: TimelineStep[], ev: GovEvent): TimelineStep[] {
  const key = ev.id ?? `${ev.step}:${ev.seq}`;
  const status: StepStatus = ev.status === "start" ? "running" : ev.status;
  const i = prev.findIndex((s) => s.key === key);
  const detail = { ...(prev[i]?.detail ?? {}), ...(ev.detail ?? {}) };
  const merged: TimelineStep = {
    key,
    // Position is **arrival order**, not `ev.seq`: a merged row keeps the position
    // it was first given, a new row goes after everything already held.
    //
    // Within one stream the two orders are identical — the counter is monotonic and
    // SSE preserves order — so this changes nothing visible. Across two streams
    // `seq` is not an ordinal at all: it comes from a process-global counter, and a
    // clarification splits a turn into the connection that pauses and the one that
    // resumes. If the server restarts while a human is answering — which
    // `langgraph dev` does on every file save — the counter restarts with it, and
    // the resumed `check`/`execute`/`stamp` arrive as 2, 4, 6 against rows numbered
    // 33-60. Sorting on that puts `stamp` above `guard` and the timeline reads
    // backwards. Arrival order also absorbs a gap in the sequence and two turns
    // interleaving their numbers in one process. There is no case where `seq` is
    // right and arrival is wrong.
    seq: i >= 0 ? prev[i].seq : nextPosition(prev, ev.seq),
    kind: ev.kind,
    step: ev.step,
    status,
    // Copy is the client's and depends on the status, so it is recomputed as the
    // row resolves. Keeping the first label — what this fold used to do, back
    // when the server sent one — pins every row to the wording of its `start`:
    // "Asked a question" would survive a decline, "Checking against governance"
    // a block. A label the wire *does* send still wins for that event.
    //
    // The cost, latent rather than live: a `start` that carried a wire label whose
    // resolve carries none loses it, falling back to client copy. The engine never
    // sends `label` (ADR 0010), so this cannot happen today — and restoring the old
    // `prev[i]?.label` precedence to "fix" it would reintroduce the stale-label bug
    // above. If the engine ever starts sending labels, carry the last wire-supplied
    // one explicitly instead of reordering these fallbacks.
    label: ev.label ?? defaultLabel({ step: ev.step, status, detail }),
    detail,
  };
  const next = i >= 0 ? prev.map((s, j) => (j === i ? merged : s)) : [...prev, merged];
  return next.sort((a, b) => a.seq - b.seq);
}

/**
 * How many repair loops the turn went through.
 *
 * A repair is a `check` row that did not pass: the model proposed a statement,
 * governance blocked it (or the check itself errored), and the model had to try
 * again. There is no `run_query` step to count instead (ADR 0010 §2), and this
 * is the better definition anyway — it counts governance decisions, not attempts.
 */
export function countRepairs(steps: TimelineStep[]): number {
  return steps.filter(
    (s) => s.step === "check" && (s.status === "blocked" || s.status === "error"),
  ).length;
}

/** One-line summary shown when the completed trace collapses. */
export function summarizeSteps(steps: TimelineStep[]): string {
  // Actions the agent took. `check` and `cap` are verdicts *about* an action, and
  // counting them here would report one SQL call as two steps.
  const actions = steps.filter((s) => s.kind === "tool" && s.step !== "check" && s.step !== "cap");
  const queries = actions.filter((s) => s.step === "execute").length;
  const repairs = countRepairs(steps);
  const parts = [plural(actions.length, "step")];
  if (queries > 0) parts.push(`${queries} quer${queries === 1 ? "y" : "ies"}`);
  if (repairs > 0) parts.push(plural(repairs, "repair"));
  return `Reasoning · ${parts.join(", ")}`;
}

/**
 * One recorded ledger entry as it lands on `answer.provenance.governance_ledger`.
 * Loosely typed — the engine owns the exact shape and it may grow; we read the
 * fields we render and pass the rest through as `detail`, so a v2 row's
 * `reason_code` / `terminal_reason` / `outcome` reach `defaultLabel` unchanged.
 */
export interface LedgerEntry {
  action?: string;
  step?: string;
  kind?: GovEvent["kind"];
  status?: string;
  verdict?: string;
  allowed?: boolean;
  attempt?: number;
  layer?: string;
  reason?: string;
  sql?: string;
  label?: string;
  [k: string]: unknown;
}

/** A recorded row is settled, so every status except `running` is legal here. */
const LEDGER_STATUSES = new Set<StepStatus>([
  "ok",
  "blocked",
  "error",
  "refused",
  "cap",
  "hit",
  "miss",
  "declined",
]);

function ledgerStatus(entry: LedgerEntry): StepStatus {
  const explicit = entry.status;
  if (explicit != null && LEDGER_STATUSES.has(explicit as StepStatus)) {
    return explicit as StepStatus;
  }
  if (entry.allowed === false) return "blocked";
  const verdict = String(entry.verdict ?? "").toLowerCase();
  if (verdict.includes("block")) return "blocked";
  if (verdict.includes("error")) return "error";
  if (verdict.includes("refus")) return "refused";
  return "ok";
}

/**
 * Map a completed ledger to the same `TimelineStep[]` the live stream produces,
 * so the audit trace and the live trace are one renderer over one data shape.
 * Returns `[]` for anything that isn't a non-empty array.
 */
export function buildStepsFromLedger(ledger: unknown): TimelineStep[] {
  if (!Array.isArray(ledger)) return [];
  return ledger
    .filter((e): e is LedgerEntry => e != null && typeof e === "object")
    .map((entry, index) => {
      const step = entry.step ?? entry.action ?? "step";
      const kind: GovEvent["kind"] =
        entry.kind ?? (step === FINAL_STEP ? "final" : isTool(step) ? "tool" : "rail");
      const status = ledgerStatus(entry);
      const detail = { ...entry };
      return {
        key: `ledger:${index}`,
        seq: index,
        kind,
        step,
        status,
        label: entry.label ?? defaultLabel({ step, status, detail }),
        detail,
      } satisfies TimelineStep;
    });
}

/** Small helpers reused by the row renderer for counts. */
export const stepCounts = { num, str };
