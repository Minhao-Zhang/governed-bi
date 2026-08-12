/**
 * Neutral placeholder fixtures — domain-agnostic, obviously synthetic.
 *
 * This is a **pure UI**: all real content (schema, corpus, graph, answers) comes
 * from the engine over the custom routes / `useStream`. The UI assumes nothing
 * about the data domain. These placeholders exist only so the component shells
 * render during the scaffold phase, when no backend is attached; they use
 * abstract names (`table_a`, `metric_total`) precisely so nothing here reads as
 * real data. When `NEXT_PUBLIC_LANGGRAPH_URL` is set, none of this is used.
 *
 * Shapes and counts still cover every UI state: a suspect column, an excluded
 * field, a low-confidence join, and a refusal.
 */

import type { ClarificationRequest } from "@/lib/clarification";
import type { GovEvent } from "@/lib/steps";
import type {
  AnswerView,
  AssetRow,
  AuditCorpus,
  AuditTrace,
  AuditTurns,
  Capabilities,
  ColumnRelated,
  ErGraph,
  KnowledgeGraph,
  SchemaSummaryResponse,
  TableView,
} from "@/lib/types";

// Two namespaces so the schema rail and cross-schema boundary are exercised
// offline. The FK columns already cross these (table_c/table_d in `billing`
// reference table_a/table_b in `sales`), so joins across them are the D15
// navigable cross-schema case. Namespace wire field is ``schema``.
const SALES = "sales";
const BILLING = "billing";

/* ── /capabilities — offline, no live model (mock mode) ──────────────────── */

export const MOCK_CAPABILITIES: Capabilities = {
  environment: "dev",
  dialect: "sqlite",
  can_edit: true,
  edit_mode: "file",
  can_stream: false, // no LangGraph Server attached in mock mode
  has_live_model: false,
  model: "offline (no model attached)",
  // Mock exercises the D15 scoped flow end-to-end; server /search stays deferred
  // (client Fuse index is the default), so can_search is false.
  can_scope: true,
  can_search: false,
  // Mock exercises the HITL clarification flow offline (trigger words in
  // use-chat.ts), so the interrupt-prompt UI mounts in preview mode.
  can_clarify: true,
};

/* ── /schema ─────────────────────────────────────────────────────────────── */

const MOCK_SCHEMA_TABLES: TableView[] = [
  {
    id: "table_a",
    physical_name: "table_a",
    schema: SALES,
    row_count: 1000,
    description: "Placeholder root table (one row per entity).",
    grain: "one row per entity",
    confidence: 0.97,
    excluded: false,
    excluded_reason: null,
    provenance_status: "certified",
    columns: [
      {
        physical_name: "id",
        physical_type: "INTEGER",
        logical_type: "id",
        nullable: false,
        is_unique: true,
        sample_values: [],
        description: "Primary key.",
        role: "primary_key",
        references: null,
        confidence: 0.99,
        reliability: "ok",
        reliability_note: null,
        excluded: false,
        excluded_reason: null,
        provenance_status: "certified",
        evidence: null,
      },
      {
        physical_name: "label",
        physical_type: "TEXT",
        logical_type: "text",
        nullable: true,
        is_unique: false,
        sample_values: [],
        description: "A display label.",
        role: "attribute",
        references: null,
        confidence: 0.9,
        reliability: "ok",
        reliability_note: null,
        excluded: false,
        excluded_reason: null,
        provenance_status: "certified",
        evidence: null,
      },
    ],
  },
  {
    id: "table_b",
    physical_name: "table_b",
    schema: SALES,
    row_count: 25000,
    description: "Placeholder fact table (one row per event).",
    grain: "one row per event",
    confidence: 0.94,
    excluded: false,
    excluded_reason: null,
    provenance_status: "certified",
    columns: [
      {
        physical_name: "id",
        physical_type: "INTEGER",
        logical_type: "id",
        nullable: false,
        is_unique: true,
        sample_values: [],
        description: "Primary key.",
        role: "primary_key",
        references: null,
        confidence: 0.99,
        reliability: "ok",
        reliability_note: null,
        excluded: false,
        excluded_reason: null,
        provenance_status: "certified",
        evidence: null,
      },
      {
        physical_name: "a_id",
        physical_type: "INTEGER",
        logical_type: "id",
        nullable: false,
        is_unique: false,
        sample_values: [],
        description: "Foreign key to table_a.",
        role: "foreign_key",
        references: "table_a.id",
        confidence: 0.92,
        reliability: "ok",
        reliability_note: null,
        excluded: false,
        excluded_reason: null,
        provenance_status: "certified",
        evidence: null,
      },
      {
        physical_name: "amount",
        physical_type: "REAL",
        logical_type: "measure",
        nullable: true,
        is_unique: false,
        sample_values: [],
        description: "A numeric measure.",
        role: "measure",
        references: null,
        confidence: 0.9,
        reliability: "ok",
        reliability_note: null,
        excluded: false,
        excluded_reason: null,
        provenance_status: "certified",
        evidence: null,
      },
      {
        physical_name: "restricted_field",
        physical_type: "TEXT",
        logical_type: "text",
        nullable: true,
        is_unique: false,
        sample_values: [],
        description: "Placeholder sensitive field, excluded from the served surface.",
        role: "attribute",
        references: null,
        confidence: 0.4,
        reliability: "suspect",
        reliability_note: "Uncertain quality; treated as sensitive.",
        excluded: true,
        excluded_reason: "Excluded by governance; never served.",
        provenance_status: "certified",
        evidence: null,
      },
    ],
  },
  {
    id: "table_c",
    physical_name: "table_c",
    schema: BILLING,
    row_count: 8000,
    description: "Placeholder secondary fact table.",
    grain: "one row per record",
    confidence: 0.9,
    excluded: false,
    excluded_reason: null,
    provenance_status: "heuristic",
    columns: [
      {
        physical_name: "a_id",
        physical_type: "INTEGER",
        logical_type: "id",
        nullable: false,
        is_unique: false,
        sample_values: [],
        description: "Foreign key to table_a.",
        role: "foreign_key",
        references: "table_a.id",
        confidence: 0.85,
        reliability: "ok",
        reliability_note: null,
        excluded: false,
        excluded_reason: null,
        provenance_status: "heuristic",
        evidence: null,
      },
      {
        physical_name: "score",
        physical_type: "INTEGER",
        logical_type: "measure",
        nullable: true,
        is_unique: false,
        sample_values: [],
        description: "A numeric score.",
        role: "measure",
        references: null,
        confidence: 0.9,
        reliability: "ok",
        reliability_note: null,
        excluded: false,
        excluded_reason: null,
        provenance_status: "heuristic",
        evidence: null,
      },
    ],
  },
  {
    id: "table_d",
    physical_name: "table_d",
    schema: BILLING,
    row_count: 40,
    description: "Placeholder dimension table.",
    grain: "one row per category",
    confidence: 0.96,
    excluded: false,
    excluded_reason: null,
    provenance_status: "certified",
    columns: [
      {
        physical_name: "id",
        physical_type: "INTEGER",
        logical_type: "id",
        nullable: false,
        is_unique: true,
        sample_values: [],
        description: "Primary key.",
        role: "primary_key",
        references: null,
        confidence: 0.99,
        reliability: "ok",
        reliability_note: null,
        excluded: false,
        excluded_reason: null,
        provenance_status: "certified",
        evidence: null,
      },
      {
        physical_name: "name",
        physical_type: "TEXT",
        logical_type: "text",
        nullable: false,
        is_unique: true,
        sample_values: [],
        description: "Category name.",
        role: "attribute",
        references: null,
        confidence: 0.95,
        reliability: "ok",
        reliability_note: null,
        excluded: false,
        excluded_reason: null,
        provenance_status: "certified",
        evidence: null,
      },
    ],
  },
];

/** `MOCK_SCHEMA` with each column's `id` filled in from the mock's own scheme.
 *
 * The live engine sends column ids on every column projection and the UI reads them from
 * there (ADR 0008 D4). The mock has to supply its own, or the column drill-down would find
 * no id and correctly report the column as unresolvable — a real message for a fake reason. */
export const MOCK_SCHEMA: TableView[] = MOCK_SCHEMA_TABLES.map((table) => ({
  ...table,
  columns: table.columns.map((column) => ({
    ...column,
    id: mockColumnId(table.id, column.physical_name),
  })),
}));

/* ── /schema/summary — lean catalog derived from MOCK_SCHEMA (D15) ───────── */
// Kept in sync with MOCK_SCHEMA by derivation so the two never drift; the api
// client filters/paginates this in mock mode. Namespace field is `schema`
// (the D15-renamed name; this route is D15-only).

export const MOCK_SCHEMA_SUMMARY: SchemaSummaryResponse = {
  total: MOCK_SCHEMA.length,
  items: MOCK_SCHEMA.map((t) => ({
    id: t.id,
    physical_name: t.physical_name,
    schema: t.schema,
    row_count: t.row_count,
    n_columns: t.columns.length,
    excluded: t.excluded,
    has_suspect: t.columns.some((c) => c.reliability === "suspect"),
    provenance_status: t.provenance_status,
    columns: t.columns.map((c) => ({
      physical_name: c.physical_name,
      physical_type: c.physical_type,
      role: c.role ?? null,
      reliability: c.reliability,
      excluded: c.excluded,
    })),
  })),
};

/* ── /graph — full knowledge graph over all asset types ──────────────────── */

export const MOCK_GRAPH: KnowledgeGraph = {
  nodes: [
    { id: "table_a", kind: "table", label: "table_a", excluded: false, provenance_status: "certified", has_suspect: false, schema: SALES },
    { id: "table_b", kind: "table", label: "table_b", excluded: false, provenance_status: "certified", has_suspect: true, schema: SALES },
    { id: "table_c", kind: "table", label: "table_c", excluded: false, provenance_status: "heuristic", has_suspect: false, schema: BILLING },
    { id: "table_d", kind: "table", label: "table_d", excluded: false, provenance_status: "certified", has_suspect: false, schema: BILLING },
    { id: "metric_total", kind: "metric", label: "metric_total", excluded: false, provenance_status: "certified", confidence: 0.95 },
    { id: "metric_average", kind: "metric", label: "metric_average", excluded: false, provenance_status: "certified", confidence: 0.9 },
    { id: "term_total", kind: "term", label: "total", excluded: false, provenance_status: "certified" },
    { id: "term_label", kind: "term", label: "label", excluded: false, provenance_status: "certified" },
    { id: "join_b_a", kind: "join", label: "table_b → table_a", excluded: false, provenance_status: "certified", confidence: 0.92 },
    { id: "join_c_a", kind: "join", label: "table_c → table_a", excluded: false, provenance_status: "heuristic", confidence: 0.85 },
    { id: "join_d_b", kind: "join", label: "table_d → table_b", excluded: false, provenance_status: "heuristic", confidence: 0.55 },
    { id: "note_boolean_flags", kind: "note", label: "boolean flags", excluded: false, provenance_status: "certified" },
    { id: "fs_001", kind: "few_shot", label: "example question", excluded: false, provenance_status: "certified" },
    { id: "neg_001", kind: "negative_example", label: "restricted field", excluded: false, provenance_status: "certified" },
  ],
  edges: [
    { id: "e1", source: "join_b_a", target: "table_b", relation: "join", confidence: 0.92, low_confidence: false },
    { id: "e2", source: "join_b_a", target: "table_a", relation: "join", confidence: 0.92, low_confidence: false },
    { id: "e3", source: "join_c_a", target: "table_c", relation: "join", confidence: 0.85, low_confidence: false },
    { id: "e4", source: "join_c_a", target: "table_a", relation: "join", confidence: 0.85, low_confidence: false },
    { id: "e5", source: "join_d_b", target: "table_d", relation: "join", confidence: 0.55, low_confidence: true },
    { id: "e6", source: "join_d_b", target: "table_b", relation: "join", confidence: 0.55, low_confidence: true },
    { id: "e7", source: "metric_total", target: "table_b", relation: "measures", confidence: null },
    { id: "e8", source: "metric_average", target: "table_c", relation: "measures", confidence: null },
    { id: "e9", source: "term_total", target: "metric_total", relation: "grounds", confidence: null },
    { id: "e10", source: "note_boolean_flags", target: "table_b", relation: "scopes", confidence: null },
    { id: "e11", source: "fs_001", target: "term_total", relation: "exemplifies", confidence: null },
  ],
};

/* ── /graph — ER (tables + joins, with FK cardinality + predicate) ───────── */
// Consistent with MOCK_SCHEMA's real FK columns so column-anchored edges resolve.
// table_d is an isolated dimension (no FK yet) — a realistic case.

export const MOCK_ER_GRAPH: ErGraph = {
  nodes: [
    { id: "table_a", physical_name: "table_a", row_count: 1000, n_columns: 2, excluded: false, has_suspect: false, schema: SALES },
    { id: "table_b", physical_name: "table_b", row_count: 25000, n_columns: 4, excluded: false, has_suspect: true, schema: SALES },
    { id: "table_c", physical_name: "table_c", row_count: 8000, n_columns: 2, excluded: false, has_suspect: false, schema: BILLING },
    { id: "table_d", physical_name: "table_d", row_count: 40, n_columns: 2, excluded: false, has_suspect: false, schema: BILLING },
  ],
  edges: [
    { id: "er_b_a", source: "table_b", target: "table_a", on: "table_b.a_id = table_a.id", cardinality: "many_to_one", confidence: 0.92, low_confidence: false },
    // table_c (billing) → table_a (sales): a curated, executable cross-schema join (D15).
    { id: "er_c_a", source: "table_c", target: "table_a", on: "table_c.a_id = table_a.id", cardinality: "many_to_one", confidence: 0.85, low_confidence: false },
    // table_d (billing) → table_b (sales): a low-confidence cross-schema join.
    { id: "er_d_b", source: "table_d", target: "table_b", on: "table_d.b_id = table_b.id", cardinality: "many_to_one", confidence: 0.55, low_confidence: true },
  ],
};

/* ── /corpus/assets ──────────────────────────────────────────────────────── */

export const MOCK_ASSETS: AssetRow[] = [
  { id: "join_b_a", asset_type: "join", summary: "table_b.a_id = table_a.id (many_to_one)", provenance_status: "certified", excluded: false },
  { id: "join_c_a", asset_type: "join", summary: "table_c.a_id = table_a.id (many_to_one)", provenance_status: "heuristic", excluded: false },
  { id: "join_d_b", asset_type: "join", summary: "table_d.b_id = table_b.id (many_to_one)", provenance_status: "heuristic", excluded: false },
  { id: "metric_total", asset_type: "metric", summary: "metric_total: SUM(table_b.amount)", provenance_status: "certified", excluded: false },
  { id: "metric_average", asset_type: "metric", summary: "metric_average: AVG(table_c.score)", provenance_status: "certified", excluded: false },
  { id: "term_total", asset_type: "term", summary: "total = total, sum, aggregate", provenance_status: "certified", excluded: false },
  { id: "term_label", asset_type: "term", summary: "label = label, name, title", provenance_status: "certified", excluded: false },
  { id: "note_boolean_flags", asset_type: "note", summary: "[business_rule] 0/1 integer columns are booleans", provenance_status: "certified", excluded: false },
  { id: "fs_001", asset_type: "few_shot", summary: "What is the total amount by category?", provenance_status: "certified", excluded: false },
  { id: "neg_001", asset_type: "negative_example", summary: "requests for excluded/restricted fields", provenance_status: "certified", excluded: false },
];

/* ── Chat: a placeholder answer + a refusal, for the mock transport ──────── */

/**
 * The ADR 0004 run-log + instrumentation block the engine stamps onto EVERY
 * terminal answer (`METADATA_PROVENANCE_KEYS` in `analyst/run_log.py`). It reaches
 * the client verbatim — `_redact_provenance_for_client` only strips ledger row
 * bodies and reasons — so the mock carries it too, or the offline drawer would
 * never exercise the grouping that keeps it from burying the governance fields.
 *
 * Values are synthetic but shape-faithful, including the deliberate `null`s: ADR
 * 0004 defaults unmeasured instrumentation to null so "not measured" stays
 * distinguishable from "measured zero".
 */
export const MOCK_RUN_RECORD: Record<string, unknown> = {
  turn_id: "turn_mock_0001",
  run_id: "run_mock_0001",
  thread_id: "thread_mock",
  producer: "serve",
  data_split: "dev",
  export_allow: true,
  corpus_release_hash: "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
  corpus_pin: "sales",
  serve_config_hash: "cfg_9f8e7d6c",
  prompt_set_hash: "ps_1a2b3c4d",
  prompt_variants: { analyst_system: "baseline", schema_pick: "baseline" },
  token_usage: [
    { source: "agent_core", model: "gpt-5.6-luna", usage_metadata: { input_tokens: 4120, output_tokens: 260, total_tokens: 4380 } },
  ],
  token_sum: { input_tokens: 4120, output_tokens: 260, total_tokens: 4380 },
  cost_est_usd: 0.01032,
  latency_ms: 3410,
  // A `register/stages.py` Outcome. It was `"finalize"` — a v1 *stage* name in the
  // outcome field, which is not a member of `answered | refused | clarification |
  // capped | crashed` and so classified as nothing at all.
  outcome: "answered",
  model: "gpt-5.6-luna",
  serve_path: "agent",
  // Stage names and per-stage detail keys are `register/stages.py`'s, matching
  // MOCK_AGENT_EVENTS above — the drawer's timing rows and the live timeline
  // describe one turn, so they may not use two vocabularies.
  stage_events: [
    { stage: "accept", status: "ok", ms: 3.1, detail: { turn_index: 2 } },
    { stage: "guard", status: "ok", ms: 1.4 },
    { stage: "rewrite", status: "ok", ms: 388.2, detail: { rewritten: true } },
    { stage: "negative_gate", status: "ok", ms: 0.9, detail: { gate: "disabled" } },
    { stage: "facet_schema", status: "ok", ms: 191.7, detail: { n_hits: 6 } },
    { stage: "facet_term", status: "ok", ms: 94.0, detail: { n_hits: 2 } },
    { stage: "facet_metric", status: "ok", ms: 88.5, detail: { n_hits: 1 } },
    { stage: "facet_entity", status: "ok", ms: 86.2, detail: { n_hits: 0 } },
    { stage: "facet_example", status: "ok", ms: 143.6, detail: { n_hits: 3 } },
    { stage: "route", status: "ok", ms: 412.5, detail: { n_candidates: 4 } },
    { stage: "resolve", status: "ok", ms: 21.8, detail: { n_pulled_in: 4 } },
    { stage: "connect", status: "ok", ms: 34.9, detail: { n_crossings: 1 } },
    { stage: "assemble", status: "ok", ms: 12.0, detail: { n_chars: 4820 } },
    { stage: "agent_core", status: "ok", ms: 1980.4, detail: { n_attempts: 2 } },
    { stage: "check", status: "blocked", ms: 8.2, detail: { attempt: 1, layer: "table" } },
    { stage: "check", status: "ok", ms: 4.6, detail: { attempt: 2 } },
    { stage: "execute", status: "ok", ms: 604.2, detail: { row_count: 4 } },
    { stage: "stamp", status: "ok", ms: 5.7, detail: { outcome: "answered" } },
  ],
  n_tool_calls: { read_body: 1, inspect_schema: 1, sample_rows: 1, check: 2, execute: 1 },
  by_guardrail_layer: { L1: 0, L2: 0, L3: 0, L5: 0 },
};

export const MOCK_ANSWER: AnswerView = {
  // Synthetic, and shaped like what the v2 engine actually emits -- a mock in the old shape
  // is worse than no mock, because it makes a broken UI look correct.
  outcome: "answered",
  text: null,
  answer_text: "Kansai 15, Great Lakes 15, Rhine-Ruhr 15, Pacific Northwest 15.",
  failed_stage: null,
  error_type: null,
  refused_by: null,
  record: {
    outcome: "answered",
    generated_sql: "SELECT r.name AS region, count(c.id) AS customers FROM gbi_demo_sales.customers c JOIN gbi_demo_sales.regions r ON c.region_id = r.id GROUP BY r.name;",
    execution: { terminal: "answered", attempts: [{ passed: true, reason_code: "passed", verdict_layer: null, path: "agent" }], guardrail_errors: 0 },
    licensed: ["gbi_demo_sales.customers", "gbi_demo_sales.regions"],
    context_hash: "907e44d8b21c2212f21e01bf79e04737d3bf5305a24aeda98d4b326d54311921",
    corpus_content_hash: "c1296e937e4d7fe0a1b2c3d4e5f60718",
    db_id: "gbi_demo_sales",
    guardrail_errors: 0,
    n_re_served: 0,
  },
};

/** Graded delivery: SQL + result present, but assurance is unverified (§13.2). */
export const MOCK_GRADED_ANSWER: AnswerView = {
  // Synthetic, and shaped like what the v2 engine actually emits -- a mock in the old shape
  // is worse than no mock, because it makes a broken UI look correct.
  outcome: "answered",
  text: null,
  answer_text: "Kansai 15, Great Lakes 15, Rhine-Ruhr 15, Pacific Northwest 15.",
  failed_stage: null,
  error_type: null,
  refused_by: null,
  record: {
    outcome: "answered",
    generated_sql: "SELECT r.name AS region, count(c.id) AS customers FROM gbi_demo_sales.customers c JOIN gbi_demo_sales.regions r ON c.region_id = r.id GROUP BY r.name;",
    execution: { terminal: "answered", attempts: [{ passed: true, reason_code: "passed", verdict_layer: null, path: "agent" }], guardrail_errors: 0 },
    licensed: ["gbi_demo_sales.customers", "gbi_demo_sales.regions"],
    context_hash: "907e44d8b21c2212f21e01bf79e04737d3bf5305a24aeda98d4b326d54311921",
    corpus_content_hash: "c1296e937e4d7fe0a1b2c3d4e5f60718",
    db_id: "gbi_demo_sales",
    guardrail_errors: 0,
    n_re_served: 0,
  },
};

/* ── A scripted v2 stage-event trajectory (ADR 0010) ──────────────────────────
 *
 * With no backend attached this is the **only** way to see the live timeline, so
 * it is a full turn rather than a sketch, and it is faithful to the emitters in
 * `serve/wrap.py`, `serve/tools.py` and `serve/nodes/stamp.py`: the same stage
 * names, the same statuses, and the same closed-vocabulary detail keys — nothing
 * here carries a field the wire does not (ADR 0010 §4 keeps result rows, driver
 * text and nested item bags off the stream, so this fixture has none).
 *
 * The shape it exercises, in order:
 *
 *  - the intake rails, including a `rewrite` that fired and the `negative_gate`
 *    reporting `gate: "disabled"` — which is what it reports on essentially every
 *    turn today, because `negative_tau` is unset until a negative corpus exists;
 *  - the five facets **interleaved**, starting together and resolving out of order,
 *    which is the concurrency the grouped timeline exists to absorb;
 *  - `route`/`resolve`/`connect`/`assemble`, with a cross-schema crossing;
 *  - the agent loop: two reads, a `check` that governance **blocks**, the repair,
 *    then a passing `check` + the `execute` that carries the statement and its
 *    digest — the pair that replaced v1's single `run_query` row;
 *  - `stamp`, the one `final` event, agreeing with the answer fixture's outcome.
 *
 * `useChat` replays it through `reduceSteps` on a timer. `seq` starts at 1 because
 * the engine's counter does (`itertools.count(1)`), and `execute` deliberately has
 * no `start`: its status is read off a completed record, never declared on entry.
 */

const AGENT_OK_SQL =
  "SELECT d.name AS category, SUM(b.amount) AS total\nFROM table_b b\nJOIN table_a a ON b.a_id = a.id\nJOIN table_d d ON d.b_id = b.id\nGROUP BY d.name\nORDER BY total DESC\nLIMIT 5;";

export const MOCK_AGENT_EVENTS: GovEvent[] = [
  /* ── intake ─────────────────────────────────────────────────────────────── */
  // `serve_path` rides the first event of the turn only, and `accept` is the first
  // node on the only path that streams.
  { seq: 1, id: "accept:turn_mock", kind: "rail", step: "accept", status: "start", serve_path: "agent" },
  { seq: 2, id: "accept:turn_mock", kind: "rail", step: "accept", status: "ok", detail: { turn_index: 2 } },
  { seq: 3, id: "guard:turn_mock", kind: "rail", step: "guard", status: "start" },
  { seq: 4, id: "guard:turn_mock", kind: "rail", step: "guard", status: "ok" },
  { seq: 5, id: "rewrite:turn_mock", kind: "rail", step: "rewrite", status: "start" },
  { seq: 6, id: "rewrite:turn_mock", kind: "rail", step: "rewrite", status: "ok", detail: { rewritten: true } },
  { seq: 7, id: "negative_gate:turn_mock", kind: "rail", step: "negative_gate", status: "start" },
  {
    seq: 8,
    id: "negative_gate:turn_mock",
    kind: "rail",
    step: "negative_gate",
    status: "ok",
    detail: { gate: "disabled" },
  },

  /* ── the five facets, concurrent ─────────────────────────────────────────── */
  { seq: 9, id: "facet_schema:turn_mock", kind: "rail", step: "facet_schema", status: "start" },
  { seq: 10, id: "facet_term:turn_mock", kind: "rail", step: "facet_term", status: "start" },
  { seq: 11, id: "facet_metric:turn_mock", kind: "rail", step: "facet_metric", status: "start" },
  { seq: 12, id: "facet_entity:turn_mock", kind: "rail", step: "facet_entity", status: "start" },
  { seq: 13, id: "facet_example:turn_mock", kind: "rail", step: "facet_example", status: "start" },
  { seq: 14, id: "facet_term:turn_mock", kind: "rail", step: "facet_term", status: "ok", detail: { n_hits: 2 } },
  { seq: 15, id: "facet_schema:turn_mock", kind: "rail", step: "facet_schema", status: "ok", detail: { n_hits: 6 } },
  { seq: 16, id: "facet_example:turn_mock", kind: "rail", step: "facet_example", status: "ok", detail: { n_hits: 3 } },
  { seq: 17, id: "facet_metric:turn_mock", kind: "rail", step: "facet_metric", status: "ok", detail: { n_hits: 1 } },
  // Zero hits, and it says so rather than going quiet: a facet that found nothing
  // is a fact about the corpus.
  { seq: 18, id: "facet_entity:turn_mock", kind: "rail", step: "facet_entity", status: "ok", detail: { n_hits: 0 } },

  /* ── post-fan-in, deterministic ──────────────────────────────────────────── */
  { seq: 19, id: "route:turn_mock", kind: "rail", step: "route", status: "start" },
  {
    seq: 20,
    id: "route:turn_mock",
    kind: "rail",
    step: "route",
    status: "ok",
    detail: { schemas: [SALES], n_candidates: 4 },
  },
  { seq: 21, id: "resolve:turn_mock", kind: "rail", step: "resolve", status: "start" },
  {
    seq: 22,
    id: "resolve:turn_mock",
    kind: "rail",
    step: "resolve",
    status: "ok",
    detail: { n_pulled_in: 4, n_licensed: 3 },
  },
  { seq: 23, id: "connect:turn_mock", kind: "rail", step: "connect", status: "start" },
  {
    seq: 24,
    id: "connect:turn_mock",
    kind: "rail",
    step: "connect",
    status: "ok",
    // One crossing: table_d lives in `billing` and the rest in `sales`.
    detail: { n_crossings: 1, n_licensed: 3 },
  },
  { seq: 25, id: "assemble:turn_mock", kind: "rail", step: "assemble", status: "start" },
  {
    seq: 26,
    id: "assemble:turn_mock",
    kind: "rail",
    step: "assemble",
    status: "ok",
    // `n_chars` only. `n_assets` was in ADR 0010's table and never emitted, and the
    // contract was corrected by deleting it — a fixture carrying it would be the
    // mock teaching the renderer to expect a field no server sends.
    detail: { n_chars: 4820 },
  },

  /* ── the agent loop ─────────────────────────────────────────────────────── */
  { seq: 27, id: "agent_core:turn_mock", kind: "rail", step: "agent_core", status: "start" },
  { seq: 28, id: "read_body:call_1", kind: "tool", step: "read_body", status: "start", detail: { n_asset_ids: 2 } },
  { seq: 29, id: "read_body:call_1", kind: "tool", step: "read_body", status: "ok", detail: { n_asset_ids: 2 } },
  {
    seq: 30,
    id: "inspect_schema:call_2",
    kind: "tool",
    step: "inspect_schema",
    status: "start",
    detail: { table_id: `${SALES}.table_b` },
  },
  {
    seq: 31,
    id: "inspect_schema:call_2",
    kind: "tool",
    step: "inspect_schema",
    status: "ok",
    detail: { table_id: `${SALES}.table_b` },
  },
  {
    seq: 32,
    id: "sample_rows:call_3",
    kind: "tool",
    step: "sample_rows",
    status: "start",
    detail: { column_id: `${SALES}.table_b.status`, limit: 5 },
  },
  {
    seq: 33,
    id: "sample_rows:call_3",
    kind: "tool",
    step: "sample_rows",
    status: "ok",
    detail: { column_id: `${SALES}.table_b.status`, limit: 5 },
  },
  // Attempt 1: blocked at the licensing layer. No `sql` on a blocked check —
  // nothing reached the database, so there is no executed statement to report.
  { seq: 34, id: "check:call_4", kind: "tool", step: "check", status: "start", detail: { attempt: 1 } },
  {
    seq: 35,
    id: "check:call_4",
    kind: "tool",
    step: "check",
    status: "blocked",
    detail: { attempt: 1, layer: "table", reason_code: "r_table_not_licensed" },
  },
  // Attempt 2: the repair clears governance and runs.
  { seq: 36, id: "check:call_5", kind: "tool", step: "check", status: "start", detail: { attempt: 2 } },
  {
    seq: 37,
    id: "check:call_5",
    kind: "tool",
    step: "check",
    status: "ok",
    detail: { attempt: 2, reason_code: "passed" },
  },
  {
    seq: 38,
    id: "execute:call_5",
    kind: "tool",
    step: "execute",
    status: "ok",
    detail: {
      sql: AGENT_OK_SQL,
      sql_sha256: "3f1c8a5e9b7d24610fe8c3a1d5b90427ec6183fa20d47b59e0c8a3f1d29b6754",
      row_count: 4,
      truncated: false,
      n_columns: 2,
    },
  },
  {
    seq: 39,
    id: "agent_core:turn_mock",
    kind: "rail",
    step: "agent_core",
    status: "ok",
    detail: { n_attempts: 2 },
  },

  /* ── terminal ───────────────────────────────────────────────────────────── */
  {
    seq: 40,
    id: "stamp:turn_mock",
    kind: "final",
    step: "stamp",
    status: "ok",
    detail: { outcome: "answered", failed_stage: null },
  },
];

export const MOCK_AGENT_ANSWER: AnswerView = {
  // Synthetic, and shaped like what the v2 engine actually emits -- a mock in the old shape
  // is worse than no mock, because it makes a broken UI look correct.
  outcome: "answered",
  text: null,
  answer_text: "Kansai 15, Great Lakes 15, Rhine-Ruhr 15, Pacific Northwest 15.",
  failed_stage: null,
  error_type: null,
  refused_by: null,
  record: {
    outcome: "answered",
    generated_sql: "SELECT r.name AS region, count(c.id) AS customers FROM gbi_demo_sales.customers c JOIN gbi_demo_sales.regions r ON c.region_id = r.id GROUP BY r.name;",
    execution: { terminal: "answered", attempts: [{ passed: true, reason_code: "passed", verdict_layer: null, path: "agent" }], guardrail_errors: 0 },
    licensed: ["gbi_demo_sales.customers", "gbi_demo_sales.regions"],
    context_hash: "907e44d8b21c2212f21e01bf79e04737d3bf5305a24aeda98d4b326d54311921",
    corpus_content_hash: "c1296e937e4d7fe0a1b2c3d4e5f60718",
    db_id: "gbi_demo_sales",
    guardrail_errors: 0,
    n_re_served: 0,
  },
};

/* ── Serve-time clarification (HITL) ─────────────────────────────────────────
 * The question the mock agent "interrupts" with when a trigger word is asked
 * (see use-chat.ts CLARIFY_PATTERN). Both a constrained pick AND freeform, to
 * exercise the full prompt (contract §3). When a backend raises a real
 * `interrupt()`, this fixture is unused. */

export const MOCK_CLARIFICATION: ClarificationRequest = {
  kind: "clarification",
  clarification_id: "clar_mock01",
  question: "Which definition of “active” did you mean?",
  why: "The corpus has two competing definitions of “active” and the question is ambiguous between them.",
  choices: [
    { id: "opt_login30", label: "Logged in within the last 30 days" },
    { id: "opt_status", label: "Account status = 'active'" },
  ],
  allow_freeform: true,
  tier: "audit",
};

/** The answer the mock resumes to once the clarification is answered; carries
 * the resolved clarification in provenance (contract §7, `answered_by:"user"`). */
export const MOCK_CLARIFIED_ANSWER: AnswerView = {
  // Synthetic, and shaped like what the v2 engine actually emits -- a mock in the old shape
  // is worse than no mock, because it makes a broken UI look correct.
  outcome: "answered",
  text: null,
  answer_text: "Kansai 15, Great Lakes 15, Rhine-Ruhr 15, Pacific Northwest 15.",
  failed_stage: null,
  error_type: null,
  refused_by: null,
  record: {
    outcome: "answered",
    generated_sql: "SELECT r.name AS region, count(c.id) AS customers FROM gbi_demo_sales.customers c JOIN gbi_demo_sales.regions r ON c.region_id = r.id GROUP BY r.name;",
    execution: { terminal: "answered", attempts: [{ passed: true, reason_code: "passed", verdict_layer: null, path: "agent" }], guardrail_errors: 0 },
    licensed: ["gbi_demo_sales.customers", "gbi_demo_sales.regions"],
    context_hash: "907e44d8b21c2212f21e01bf79e04737d3bf5305a24aeda98d4b326d54311921",
    corpus_content_hash: "c1296e937e4d7fe0a1b2c3d4e5f60718",
    db_id: "gbi_demo_sales",
    guardrail_errors: 0,
    n_re_served: 0,
  },
};

export const MOCK_REFUSAL: AnswerView = {
  // Synthetic, and shaped like what the v2 engine actually emits -- a mock in the old shape
  // is worse than no mock, because it makes a broken UI look correct.
  outcome: "refused",
  text: "This question can't be answered as asked: it needs a table this corpus does not license.",
  answer_text: null,
  failed_stage: null,
  error_type: null,
  refused_by: "check",
  record: {
    outcome: "refused",
    generated_sql: null,
    execution: { terminal: "refused", attempts: [{ passed: false, reason_code: "r_table_not_licensed", verdict_layer: 3, path: "agent" }], guardrail_errors: 0 },
    licensed: ["gbi_demo_sales.customers", "gbi_demo_sales.regions"],
    context_hash: "907e44d8b21c2212f21e01bf79e04737d3bf5305a24aeda98d4b326d54311921",
    corpus_content_hash: "c1296e937e4d7fe0a1b2c3d4e5f60718",
    db_id: "gbi_demo_sales",
    guardrail_errors: 0,
    n_re_served: 0,
  },
};

/* ── /columns/{column_id}/related (§14) — derived from the mock schema/graph ──
 * So the click-a-column feature is demoable offline. Resolves the column out of
 * MOCK_SCHEMA and synthesizes plausible terms/rules/joins/metrics/FKs from the
 * existing mock assets (all lists mirror the live contract shape). */

const EMPTY_COLUMN_RELATED = (columnId: string): ColumnRelated => ({
  column: {
    id: columnId,
    table_id: "",
    table_physical_name: "",
    schema: null,
    physical_name: "",
  },
  terms: [],
  rules: [],
  fk_out: null,
  fk_in: [],
  joins: [],
  metrics: [],
  meta: { column_resolvable: false },
});

/** Metric ids by base table, mirroring MOCK_ASSETS metric summaries. */
const MOCK_METRICS_BY_TABLE: Record<string, { id: string; name: string }[]> = {
  table_b: [{ id: "metric_total", name: "metric_total" }],
  table_c: [{ id: "metric_average", name: "metric_average" }],
};

/**
 * A column id for MOCK data only — deliberately not a mirror of the engine's.
 *
 * This lived in `lib/columns.ts` as `deriveColumnId`, was used by the real detail sheet, and
 * produced v1's `col_<table>_<physical>` while the engine mints
 * `{table_id}.{slug(physical_name)}` (ADR 0008 D1) — so every id the sheet asked for was one
 * the engine had never heard of. The live path now reads ids from the payload, and this
 * survives only because the mock has to invent its own consistent ids. It uses the simple
 * dotted form because mock names never need `slug`'s sanitising hash; do not call it on live
 * data, where that assumption does not hold.
 */
function mockColumnId(tableId: string, physicalName: string): string {
  return `${tableId}.${physicalName}`;
}

export function mockColumnRelated(columnId: string): ColumnRelated {
  // Locate the (table, column) whose derived id matches.
  let hit: { table: TableView; physical: string } | null = null;
  for (const table of MOCK_SCHEMA) {
    for (const col of table.columns) {
      if (mockColumnId(table.id, col.physical_name) === columnId) {
        hit = { table, physical: col.physical_name };
        break;
      }
    }
    if (hit) break;
  }
  if (!hit) return EMPTY_COLUMN_RELATED(columnId);

  const { table, physical } = hit;
  const col = table.columns.find((c) => c.physical_name === physical)!;
  const qualified = `${table.physical_name}.${physical}`;

  // fk_out: this column's own reference (mock uses "physical_table.column").
  let fk_out: ColumnRelated["fk_out"] = null;
  if (col.references) {
    const [refPhysical, refCol] = col.references.split(".");
    const refTable = MOCK_SCHEMA.find((t) => t.physical_name === refPhysical);
    if (refTable && refCol) {
      fk_out = {
        column_id: mockColumnId(refTable.id, refCol),
        table_id: refTable.id,
        physical_name: refCol,
      };
    }
  }

  // fk_in: columns elsewhere that reference this one.
  const fk_in: ColumnRelated["fk_in"] = [];
  for (const t of MOCK_SCHEMA) {
    for (const c of t.columns) {
      if (c.references === qualified) {
        fk_in.push({
          column_id: mockColumnId(t.id, c.physical_name),
          table_id: t.id,
          physical_name: c.physical_name,
        });
      }
    }
  }

  // joins: ER edges whose ON predicate touches this column (server-resolved live).
  const joins: ColumnRelated["joins"] = MOCK_ER_GRAPH.edges
    .filter((e) => e.on.includes(qualified))
    .map((e) => ({
      id: e.id,
      left_table: e.source,
      right_table: e.target,
      other_table_id: e.source === table.id ? e.target : e.source,
      on: e.on,
      cardinality: e.cardinality,
      confidence: e.confidence,
      low_confidence: e.low_confidence,
    }));

  // terms bound to this column (mock: measures ground "total", label → "label").
  const terms: ColumnRelated["terms"] = [];
  if (col.role === "measure") {
    terms.push({ id: "term_total", name: "total", synonyms: ["sum", "aggregate"], confidence: 0.9, provenance_status: "certified" });
  } else if (physical === "label") {
    terms.push({ id: "term_label", name: "label", synonyms: ["name", "title"], confidence: 0.85, provenance_status: "certified" });
  }

  // notes scoping this column (wire key remains `rules`; handoff §14).
  const rules: ColumnRelated["rules"] =
    table.id === "table_b"
      ? [{ id: "note_boolean_flags", kind: "business_rule", statement: "0/1 integer columns are booleans.", confidence: 0.8, provenance_status: "certified" }]
      : [];

  // metrics on this table (table-grain only, §14.4).
  const metrics: ColumnRelated["metrics"] = (MOCK_METRICS_BY_TABLE[table.id] ?? []).map((m) => ({
    ...m,
    granularity: "table",
  }));

  return {
    column: {
      id: columnId,
      table_id: table.id,
      table_physical_name: table.physical_name,
      schema: table.schema,
      physical_name: physical,
    },
    terms,
    rules,
    fk_out,
    fk_in,
    joins,
    metrics,
    meta: { column_resolvable: true },
  };
}

/* ── the audit surface ─────────────────────────────────────────────────────── */
//
// Neutral placeholders, and deliberately *empty* rather than plausible: a mock turn
// list with rows in it would make the audit page look like it had found real traffic
// on a machine with no backend, which is the stub-answer shape this project keeps
// removing. `n: 0` is the honest mock, and the page renders its own empty state.

export const MOCK_AUDIT_TURNS: AuditTurns = {
  turns: [],
  meta: { n: 0, log_dir: "(no backend)", columns: [] },
};

export const MOCK_AUDIT_TRACE: AuditTrace = {
  found: false,
  turn_id: "(no backend)",
  question: null,
  answer_text: null,
  outcome: null,
  asked_at: null,
  stages: [],
  ledger: [],
  terminal: null,
  missing_required: [],
  undeclared_keys: [],
};

export const MOCK_AUDIT_CORPUS: AuditCorpus = {
  corpus_content_hash: null,
  assets: { total: 0, by_type: {} },
  schemas: [],
  structure: {
    join_edges: 0,
    references: 0,
    schema_tags: 0,
    untagged_assets: 0,
    table_pairs_with_joins: 0,
  },
  problems: { fatal: [], degradations: [], n_fatal: 0, n_degradations: 0 },
  servable: false,
};
