/**
 * Grouping the provenance record for the audit drawer.
 *
 * `AnswerResponse.provenance` is an open `Record` and the engine keeps widening
 * it. ADR 0004 (run logging) now stamps ~21 keys onto *every* terminal answer via
 * `finalize_and_log` — `METADATA_PROVENANCE_KEYS` in `analyst/run_log.py` — and
 * `_redact_provenance_for_client` only strips ledger row bodies and reasons, so
 * all of them reach us verbatim. Dumped flat, that block buries the handful of
 * fields a reviewer actually opens the drawer for, and `stage_events` /
 * per-source `token_usage` land as unreadable `JSON.stringify` blobs (the
 * latter is folded into a one-line `token_sum`).
 *
 * So the drawer reads through three named groups instead of one list:
 *
 *  - **Governance** — the answer's own decisions (route, joins, flags, refusal).
 *  - **Stages** — per-turn instrumentation: timings and counters.
 *  - **Run record** — ADR 0004 operator/eval metadata (ids, hashes, cost, tokens).
 *
 * Anything unrecognized falls through to a fourth catch-all group, so a key the
 * engine adds tomorrow is still shown today — never silently dropped.
 */

/** Reviewer-first: what the engine decided about this answer. */
const GOVERNANCE_KEYS = [
  "route",
  "bound_terms",
  "metric_id",
  "tables_used",
  // Assemble-time seed license (which tables the guardrails will permit this turn).
  "licensed_tables",
  "join_ids",
  "min_join_confidence",
  "attempts",
  "uncertainty_flags",
  "clarifications",
  "graded_delivery",
  "coverage_best_effort",
  "routed_schemas",
  // Which ranking channel actually ran: a silent `bm25_fallback` roughly halves
  // schema-routing recall, so it belongs beside the routing fields (handoff §13.6).
  "schema_route_channel",
  "selected_schema",
  "candidate_schemas",
  "suspect_columns",
  "cache_hit",
  "refused_by",
  "negative_example",
] as const;

/** Per-turn instrumentation (`_INSTRUMENTATION_KEYS`, minus the two that read as
 * governance and are grouped above: `cache_hit` / `attempts`). */
const STAGE_KEYS = ["stage_events", "n_tool_calls", "by_guardrail_layer"] as const;

/** ADR 0004 run-log metadata: identity, pins, prompt set, cost/latency.
 * Per-source `token_usage` is hidden — folded into the one `token_sum` row. */
const RUN_RECORD_KEYS = [
  "outcome",
  "latency_ms",
  "cost_est_usd",
  "token_sum",
  "model",
  "serve_path",
  "producer",
  "turn_id",
  "run_id",
  "thread_id",
  "data_split",
  "export_allow",
  "corpus_release_hash",
  "corpus_pin",
  "serve_config_hash",
  "prompt_set_hash",
  "prompt_variants",
] as const;

/** Rendered as the dedicated "Steps" timeline, not as a key/value row.
 * `token_usage` is the per-source dump — folded into `token_sum` below. */
const HIDDEN_KEYS = new Set<string>(["governance_ledger", "token_usage"]);

export interface ProvenanceGroup {
  id: "governance" | "stages" | "run" | "other";
  title: string;
  /** Operator metadata starts collapsed; governance never does. */
  collapsed: boolean;
  entries: [string, unknown][];
}

function pick(provenance: Record<string, unknown>, keys: readonly string[]): [string, unknown][] {
  return keys.filter((k) => k in provenance).map((k) => [k, provenance[k]]);
}

/**
 * Split the provenance record into the drawer's groups. Empty groups are dropped;
 * the "other" group collects every key none of the lists claim, so new engine
 * fields surface without a frontend change.
 */
export function groupProvenance(provenance: Record<string, unknown>): ProvenanceGroup[] {
  const claimed = new Set<string>([
    ...GOVERNANCE_KEYS,
    ...STAGE_KEYS,
    ...RUN_RECORD_KEYS,
    ...HIDDEN_KEYS,
  ]);
  const other = Object.keys(provenance)
    .filter((k) => !claimed.has(k))
    .sort()
    .map((k) => [k, provenance[k]] as [string, unknown]);

  // Prefer summing the detailed `token_usage` snapshots (includes cache_read)
  // into the single `token_sum` row the drawer shows.
  const runEntries = pick(provenance, RUN_RECORD_KEYS).map(([key, value]) => {
    if (key !== "token_sum") return [key, value] as [string, unknown];
    const fromUsage = summarizeTokenUsage(provenance.token_usage);
    if (fromUsage) return [key, provenance.token_usage] as [string, unknown];
    return [key, value] as [string, unknown];
  });
  // If the engine omitted token_sum but sent token_usage, still show one row.
  if (!("token_sum" in provenance) && summarizeTokenUsage(provenance.token_usage)) {
    runEntries.push(["token_sum", provenance.token_usage]);
  }

  const groups: ProvenanceGroup[] = [
    {
      id: "governance",
      title: "Governance",
      collapsed: false,
      entries: pick(provenance, GOVERNANCE_KEYS),
    },
    { id: "stages", title: "Stages", collapsed: true, entries: pick(provenance, STAGE_KEYS) },
    { id: "run", title: "Run record", collapsed: true, entries: runEntries },
    { id: "other", title: "Other", collapsed: true, entries: other },
  ];
  return groups.filter((g) => g.entries.length > 0);
}

/* ── stage_events ────────────────────────────────────────────────────────── */

/** One `StageRecorder` record: `{stage, status, ms, detail}` (governance.py). */
export interface StageEvent {
  stage: string;
  status: string;
  /** `null` for a deliberately skipped stage — a stage that never ran did NOT
   * take zero milliseconds, and the engine is careful to say so. Preserve that. */
  ms: number | null;
  detail?: Record<string, unknown>;
}

/** Parse `provenance.stage_events` defensively; `[]` when absent or malformed. */
export function stageEvents(value: unknown): StageEvent[] {
  if (!Array.isArray(value)) return [];
  const out: StageEvent[] = [];
  for (const raw of value) {
    if (raw == null || typeof raw !== "object") continue;
    const e = raw as Record<string, unknown>;
    if (typeof e.stage !== "string") continue;
    out.push({
      stage: e.stage,
      status: typeof e.status === "string" ? e.status : "ok",
      ms: typeof e.ms === "number" ? e.ms : null,
      detail:
        e.detail != null && typeof e.detail === "object"
          ? (e.detail as Record<string, unknown>)
          : undefined,
    });
  }
  return out;
}

/** Total measured stage time. Skipped stages (`ms: null`) contribute nothing. */
export function stagesTotalMs(events: StageEvent[]): number {
  return events.reduce((sum, e) => sum + (e.ms ?? 0), 0);
}

/* ── value formatting ────────────────────────────────────────────────────── */

/** `{input_tokens: 1, …}` → `input_tokens 1 · output_tokens 2`; keeps a flat
 * count/hash map readable instead of stringifying it. */
function formatFlatRecord(value: Record<string, unknown>): string {
  const parts = Object.entries(value).map(([k, v]) => `${k} ${renderScalar(v)}`);
  return parts.length > 0 ? parts.join(" · ") : "—";
}

function renderScalar(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

function isFlatRecord(value: Record<string, unknown>): boolean {
  return Object.values(value).every(
    (v) => v === null || ["string", "number", "boolean"].includes(typeof v),
  );
}

/** Pull a non-negative int from a usage blob under any of the common aliases. */
function usageInt(usage: Record<string, unknown>, ...keys: string[]): number {
  for (const key of keys) {
    const v = usage[key];
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  return 0;
}

/**
 * Collapse ADR 0004 `token_usage` (per-source snapshots) or a flat `token_sum`
 * into `input N · output N · cache N`. Cache is `cache_read` when present.
 */
export function summarizeTokenUsage(value: unknown): string | null {
  let input = 0;
  let output = 0;
  let cache = 0;
  let saw = false;

  const add = (usage: Record<string, unknown>) => {
    saw = true;
    input += usageInt(usage, "input_tokens", "prompt_tokens");
    output += usageInt(usage, "output_tokens", "completion_tokens");
    cache += usageInt(usage, "cache_read", "cache_read_input_tokens", "cache_tokens");
  };

  if (Array.isArray(value)) {
    for (const entry of value) {
      if (entry == null || typeof entry !== "object") continue;
      const row = entry as Record<string, unknown>;
      const meta = row.usage_metadata;
      if (meta != null && typeof meta === "object") add(meta as Record<string, unknown>);
      else add(row);
    }
  } else if (value != null && typeof value === "object") {
    add(value as Record<string, unknown>);
  } else {
    return null;
  }

  if (!saw) return null;
  const parts = [`input ${input}`, `output ${output}`];
  if (cache > 0) parts.push(`cache ${cache}`);
  return parts.join(" · ");
}

/** Format one provenance value for a key/value row. Flat maps become `k v · k v`;
 * everything non-scalar falls back to JSON so nothing is hidden from the audit. */
export function formatProvenanceValue(key: string, value: unknown): string {
  if (value === null || value === undefined) {
    // ADR 0004 defaults unmeasured instrumentation to null on purpose: "not
    // measured" is a distinct claim from "measured zero". Say which one it is.
    return "not measured";
  }
  if (key === "latency_ms" && typeof value === "number") return `${value} ms`;
  if (key === "cost_est_usd" && typeof value === "number") return `$${value.toFixed(6)}`;
  // Quiet metadata: BM25 fallback roughly halves routing recall vs embedding.
  if (key === "schema_route_channel" && value === "bm25_fallback") {
    return "bm25_fallback (degraded ranking)";
  }
  if (key === "token_usage" || key === "token_sum") {
    const summary = summarizeTokenUsage(value);
    if (summary) return summary;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    if (value.every((v) => ["string", "number", "boolean"].includes(typeof v))) {
      return value.map(renderScalar).join(", ");
    }
    return JSON.stringify(value);
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    return isFlatRecord(record) ? formatFlatRecord(record) : JSON.stringify(record);
  }
  return renderScalar(value);
}
