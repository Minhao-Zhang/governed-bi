/**
 * Grouping the provenance record for the audit drawer.
 *
 * The drawer's input is `answer.record` (`provenanceOf` in `answer-delivery.ts`) — the
 * turn record the engine's `stamp` node projects against `register/record.py`'s declared
 * register (ADR 0005 §4). It is an open `Record` on this side and the engine keeps widening
 * it; **nothing is redacted** on the way out (`register/record.py` records why the redaction
 * column was removed). Dumped flat, that block buries the handful of fields a reviewer
 * actually opens the drawer for, and the wide ones — `usage`, `knobs_resolved`, `facet_hits`
 * — land as unreadable `JSON.stringify` blobs (`usage` is folded into one summary line).
 *
 * So the drawer reads through three named groups instead of one list:
 *
 *  - **Governance** — what the engine decided and how the turn ended.
 *  - **Instrumentation** — degradation counters, tokens, latency.
 *  - **Run record** — identity and treatment: ids, pins, hashes.
 *
 * Anything unrecognized falls through to a fourth catch-all group, so a key the
 * engine adds tomorrow is still shown today — never silently dropped.
 *
 * **The three lists are the register's six tiers, paired.** `Tier` is declared in
 * `register/record.py` with a stated meaning per member, so pairing tiers is the one grouping
 * that cannot disagree with the engine about what a field is *for*:
 *
 * | group | tiers |
 * |---|---|
 * | Governance | `outcome` + `decision` |
 * | Instrumentation | `health` + `cost` |
 * | Run record | `identity` + `treatment` |
 *
 * A pure client cannot import the register, so the names below are copied by hand and
 * `tests/api/test_provenance_groups_match_the_register.py` fails the build when the copy and
 * the register disagree in either direction. Do not add a key here without a register row.
 *
 * This replaced a set of lists written against the deleted v1 `analyst/run_log.py`. Measured
 * 2026-08-12 before the fix: 32 of the listed keys were names the v2 record never emits and 35
 * of the register's 41 fields were on no list, so the three named groups were near-empty and
 * everything real landed in the catch-all.
 */

/** `Tier.outcome` then `Tier.decision` — how the turn ended, then what decided it.
 * `guardrail_errors` reads as governance and is deliberately **not** here: the register files
 * it under `health`, because it is a degradation counter feeding a quotability gate. */
const GOVERNANCE_KEYS = [
  // Tier.outcome
  "outcome",
  "terminal_reason",
  "failed_stage",
  "error_type",
  // Why an answered-but-wrong turn was wrong, from the eval classifier. Null on every served
  // turn -- only the eval harness has the answer key -- so this renders as not-applicable in
  // the drawer, and is here because the register declares it and this list must partition the
  // register, not because a chat turn ever carries a value. Keep comments in this array free of
  // double-quoted text: the conformance test parses the quoted names out with a regex.
  "failure_cause",
  "generated_sql",
  // Tier.decision, in register order — which is pipeline order: retrieve, route, budget,
  // license, connect, rewrite, guard, then how the attempt actually went.
  "facet_hits",
  "facet_degraded",
  "schema_ranking",
  "schemas",
  "budget_dropped",
  "budget_best_dropped_score",
  "pulled_in",
  "licensed",
  "crossings",
  "lexical_coverage",
  "rewrite",
  "guard",
  "negative",
  "abstention",
  "execution",
  "reflect_verdict",
  "n_re_served",
] as const;

/** `Tier.health` then `Tier.cost` — what degraded, and what the turn spent. */
const INSTRUMENTATION_KEYS = [
  // Tier.health
  "facet_channels",
  "guardrail_errors",
  // Tier.cost
  "usage",
  "cache_read_tokens",
  "cache_write_tokens",
  "latency_sec",
] as const;

/** `Tier.identity` then `Tier.treatment` — what joins this turn to a run, and what the
 * delivery gate reads to decide two turns are comparable. */
const RUN_RECORD_KEYS = [
  // Tier.identity
  "run_id",
  "turn_id",
  "thread_id",
  "question_id",
  "db_id",
  "attempt_id",
  // Tier.treatment, in register order
  "evicted",
  "context_hash",
  "delivery_hash",
  "tool_delivered",
  "corpus_content_hash",
  "prompt_set_hash",
  "knobs_resolved",
] as const;

/** Rendered elsewhere, so suppressed as a key/value row. `execution` is the attempt ledger
 * the "Steps" section replays; it stays in `GOVERNANCE_KEYS` too, because that section only
 * renders when `buildStepsFromLedger` finds something to draw and the audit must not lose it
 * when that returns empty — which today it always does (see the drawer's note). */
const HIDDEN_KEYS = new Set<string>([]);

export interface ProvenanceGroup {
  id: "governance" | "instrumentation" | "run" | "other";
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
    ...INSTRUMENTATION_KEYS,
    ...RUN_RECORD_KEYS,
    ...HIDDEN_KEYS,
  ]);
  const other = Object.keys(provenance)
    .filter((k) => !claimed.has(k))
    .sort()
    .map((k) => [k, provenance[k]] as [string, unknown]);

  const groups: ProvenanceGroup[] = [
    {
      id: "governance",
      title: "Governance",
      collapsed: false,
      entries: pick(provenance, GOVERNANCE_KEYS),
    },
    {
      id: "instrumentation",
      title: "Instrumentation",
      collapsed: true,
      entries: pick(provenance, INSTRUMENTATION_KEYS),
    },
    { id: "run", title: "Run record", collapsed: true, entries: pick(provenance, RUN_RECORD_KEYS) },
    { id: "other", title: "Other", collapsed: true, entries: other },
  ];
  return groups.filter((g) => g.entries.length > 0);
}

/** The register field names this module claims, for the conformance test to read. */
export const CLAIMED_KEYS = {
  governance: GOVERNANCE_KEYS,
  instrumentation: INSTRUMENTATION_KEYS,
  run: RUN_RECORD_KEYS,
} as const;

/**
 * What a `null` means, per field — the register's `Absence` column, copied by hand and held
 * to the register by the same test as the key lists.
 *
 * **All three encode as JSON `null` and this map is the only thing distinguishing them**, which
 * is `register/record.py`'s own words. Rendering every null as "not measured" is a claim the
 * client cannot back: on an answered turn `generated_sql` is null because there was no SQL, and
 * "not measured" says the engine failed to record it. `never` is the interesting third case —
 * the register says that field cannot be absent, so a null there is a defect, not a value.
 */
const ABSENCE: Record<string, "never" | "not_measured" | "not_applicable"> = {
  // Tier.outcome
  outcome: "never",
  terminal_reason: "not_applicable",
  failed_stage: "not_applicable",
  error_type: "not_applicable",
  failure_cause: "not_applicable",
  generated_sql: "not_applicable",
  // Tier.decision
  facet_hits: "not_applicable",
  facet_degraded: "not_applicable",
  schema_ranking: "not_applicable",
  schemas: "not_applicable",
  budget_dropped: "not_applicable",
  budget_best_dropped_score: "not_applicable",
  pulled_in: "not_applicable",
  licensed: "not_applicable",
  crossings: "not_applicable",
  lexical_coverage: "not_measured",
  rewrite: "not_applicable",
  guard: "never",
  negative: "not_applicable",
  abstention: "not_applicable",
  execution: "never",
  reflect_verdict: "not_measured",
  n_re_served: "never",
  // Tier.health
  facet_channels: "not_applicable",
  guardrail_errors: "never",
  // Tier.cost
  usage: "never",
  cache_read_tokens: "not_measured",
  cache_write_tokens: "not_measured",
  latency_sec: "not_measured",
  // Tier.identity
  run_id: "never",
  turn_id: "never",
  thread_id: "never",
  question_id: "never",
  db_id: "never",
  attempt_id: "never",
  // Tier.treatment
  evicted: "not_applicable",
  context_hash: "not_applicable",
  delivery_hash: "not_applicable",
  tool_delivered: "not_applicable",
  corpus_content_hash: "never",
  prompt_set_hash: "never",
  knobs_resolved: "never",
};

/* ── stage_events ────────────────────────────────────────────────────────── */

/** One stage record: `{stage, status, ms, detail}`.
 *
 * **`stage_events` is not a register field**, so no logged turn carries one and the drawer's
 * stage-timings section never renders from the audit path. Kept because the shape is the one
 * the live stream emits (ADR 0010) and this is where it would be parsed if the record ever
 * carried it; see the drawer's own note on why "live == audit" is an intention. */
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
 * Collapse the register's `usage` — an array of one record per model call, including the
 * facet and rewrite calls — into `input N · output N · cache N`.
 *
 * The alias lists are deliberately wide: `usage` rows carry `cache_read_tokens`, but the same
 * summary is applied to whatever a provider hands back, and LangChain's `usage_metadata` spells
 * the same three counts three other ways. An unrecognised spelling reads as 0, never as absent.
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
    cache += usageInt(
      usage,
      "cache_read_tokens",
      "cache_read",
      "cache_read_input_tokens",
      "cache_tokens",
    );
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
    // Three different claims arrive as one `null`; say which. ADR 0005 §6 requires "not
    // measured" stay distinguishable from "measured zero", and this is the other half of it:
    // "not applicable" must stay distinguishable from "not measured" too.
    switch (ABSENCE[key]) {
      case "not_applicable":
        return "n/a";
      case "never":
        // The register says this field cannot be absent. A null here is a recording defect,
        // and an audit surface that renders it as an ordinary blank hides one.
        return "MISSING (required)";
      case "not_measured":
        return "not measured";
      default:
        // Not a register field — the catch-all group. Claim nothing about why it is empty.
        return "—";
    }
  }
  // `latency_sec`, not `latency_ms`: the register records wall clock in seconds. There is no
  // currency row to format — `measure/price.py` is deleted and the record carries no USD.
  if (key === "latency_sec" && typeof value === "number") return `${value.toFixed(2)} s`;
  if (key === "usage") {
    const summary = summarizeTokenUsage(value);
    // An empty `usage` is a measured zero-model-call turn, not an absence. Say so rather than
    // dropping to the JSON branch, which would render `[]`.
    if (summary) return summary;
    if (Array.isArray(value) && value.length === 0) return "no model calls";
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
