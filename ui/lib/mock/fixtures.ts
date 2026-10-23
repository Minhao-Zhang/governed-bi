/**
 * Synthetic fixtures for mock mode (no `NEXT_PUBLIC_LANGGRAPH_URL`), shaped like what the engine
 * emits, with obviously abstract names so nothing reads as real data. Unused with a backend.
 */

import type { ClarificationRequest } from "@/lib/clarification";
import type { GovEvent } from "@/lib/steps";
import type { AnswerView, Capabilities, ExampleQuestion } from "@/lib/types";

const SALES = "sales";

/* ── /capabilities — offline, no live model (mock mode) ──────────────────── */

export const MOCK_CAPABILITIES: Capabilities = {
  environment: "local",
  dialect: "postgres",
  can_edit: false,
  edit_mode: "none",
  can_stream: false, // no LangGraph Server attached in mock mode
  has_live_model: false,
  model: "offline (no model attached)",
  // Mock exercises the HITL clarification flow offline (trigger words in
  // use-chat.ts), so the interrupt-prompt UI mounts in preview mode.
  can_clarify: true,
  // No live model, so the chat surfaces report no id.
  models: {
    agent: { id: null, provider: null, effort: null },
    utility: { id: null, provider: null, effort: null },
    embedding: { id: "deterministic:mock", provider: "deterministic", dimensions: 64 },
  },
  connection: { dialect: "postgres", database: "gbi_demo_sales" },
};

/* ── Chat: a placeholder answer + a refusal, for the mock transport ──────── */

/**
 * The turn record `finish` projects onto every terminal answer. Keys and value shapes are the
 * register's (`register/record.py`); only the values are synthetic. The `null`s keep "not
 * measured" apart from "measured zero" (ADR 0005 §6).
 */
export const MOCK_RUN_RECORD: Record<string, unknown> = {
  /* Tier.identity */
  run_id: "93aacb3752844310",
  turn_id: "272a8b9f34e7fac6",
  thread_id: "019ff347-e719-70b3-809a-ecb1dbfebb16",
  question_id: "c79749267012c5de",
  db_id: "BIRD-corpus",
  attempt_id: "ca9bed0e39d0bb07",

  /* Tier.setting under test */
  evicted: null,
  context_hash: "68de872dea5744c98bf0a24eb723396d215590a6ae68abb82989fbb4283e54ff",
  delivery_hash: "f04255af03ff1ce43b05502196def2030e1c5d76ab0108516193535c90b8b299",
  tool_delivered: {},
  corpus_content_hash: "86ed1dbfef8b325e188061229b665c4918ec8c86c65e39b619a5495b0abab6d5",
  prompt_set_hash: "b1f9e4d7d230cb97",
  settings_resolved: { summary_max_chars: 250, schema_top_n: 3, lexical_weight: 0.5 },

  /* Tier.decision */
  channel_hits: {
    channel_schema: { queries: ["customers per region"], hits: [{ asset_id: "gbi_demo_sales.customers", asset_type: "table", lexical: 2.9, semantic: 0.81 }] },
  },
  channel_degraded: true,
  schema_ranking: [["gbi_demo_sales", 2.9], ["gbi_demo_support", 2.62], ["gbi_demo_hr", 1.14]],
  schemas: ["gbi_demo_sales"],
  pulled_in: { "gbi_demo_sales.customers.region_id": "resolve", "gbi_demo_sales.regions.id": "connect" },
  retrieved_tables: ["gbi_demo_sales.customers", "gbi_demo_sales.regions"],
  crossings: [],
  lexical_coverage: 0.8,
  rewrite: null,
  guard: { outcome: "clear", rule_id: null, detail: null },
  negative: { outcome: "disabled", tau: null, top_score: null, matched_id: null },
  execution: { attempts: [], terminal: "no_sql", guardrail_errors: 0 },
  n_re_served: 0,

  /* Tier.outcome */
  outcome: "answered",
  terminal_reason: null,
  failed_stage: null,
  error_type: null,
  generated_sql: null,

  /* Tier.cost */
  usage: [
    { turn_index: 1, stage: "guard", model: "gpt-5.6-luna", input_tokens: 134, output_tokens: 4, cache_read_tokens: 0, cache_write_tokens: 0 },
    { turn_index: 1, stage: "agent_core", model: "gpt-5.6-luna", input_tokens: 4120, output_tokens: 260, cache_read_tokens: 10060, cache_write_tokens: 10287 },
  ],
  cache_read_tokens: 10060,
  cache_write_tokens: 10287,
  latency_sec: 39.31,
  assumptions: [
    {
      statement: "Closed businesses are included.",
      about: "closed",
      basis: "layer",
      asset_id: "gbi_demo_sales.term.closed_business",
    },
    {
      statement: "Counts are of distinct customers.",
      about: null,
      basis: "convention",
      asset_id: null,
    },
    {
      statement: "Active means logged in within 30 days.",
      about: "active",
      basis: "guess",
      asset_id: null,
    },
    {
      statement: "Unmatched regions are dropped.",
      about: null,
      basis: "found_in_query",
      asset_id: null,
    },
  ],

  /* Tier.health */
  channel_scoring: {
    channel_schema: { lexical: "ran", semantic: "ran" },
    channel_entity: { lexical: "ran", semantic: "failed" },
  },
  guardrail_errors: 0,
};

/** The rows a turn keeps. `row_count` is the full result; `rows` is the preview. */
export const MOCK_RESULT_TABLE = {
  columns: ["region", "customers"],
  rows: [
    ["Kansai", 15],
    ["Great Lakes", 15],
  ],
  row_count: 1204,
  truncated: true,
  preview_rows: 2,
};

export const MOCK_ANSWER: AnswerView = {
  outcome: "answered",
  text: null,
  answer_text: "Kansai 15, Great Lakes 15, Rhine-Ruhr 15, Pacific Northwest 15.",
  failed_stage: null,
  error_type: null,
  refused_by: null,
  record: {
    // The register-shaped block first, then this answer's own values, which win.
    ...MOCK_RUN_RECORD,
    outcome: "answered",
    generated_sql: "SELECT r.name AS region, count(c.id) AS customers FROM gbi_demo_sales.customers c JOIN gbi_demo_sales.regions r ON c.region_id = r.id GROUP BY r.name;",
    execution: { terminal: "answered", attempts: [{ passed: true, reason_code: "passed", result_layer: null, path: "agent" }], guardrail_errors: 0 },
    retrieved_tables: ["gbi_demo_sales.customers", "gbi_demo_sales.regions"],
    context_hash: "907e44d8b21c2212f21e01bf79e04737d3bf5305a24aeda98d4b326d54311921",
    corpus_content_hash: "c1296e937e4d7fe0a1b2c3d4e5f60718",
    db_id: "gbi_demo_sales",
    guardrail_errors: 0,
    n_re_served: 0,
  },
  result_table: MOCK_RESULT_TABLE,
};

/** Graded delivery: SQL + result present, but assurance is unverified (§13.2). */
export const MOCK_GRADED_ANSWER: AnswerView = {
  outcome: "answered",
  text: null,
  answer_text: "Kansai 15, Great Lakes 15, Rhine-Ruhr 15, Pacific Northwest 15.",
  failed_stage: null,
  error_type: null,
  refused_by: null,
  record: {
    // The register-shaped block first, then this answer's own values, which win.
    ...MOCK_RUN_RECORD,
    outcome: "answered",
    generated_sql: "SELECT r.name AS region, count(c.id) AS customers FROM gbi_demo_sales.customers c JOIN gbi_demo_sales.regions r ON c.region_id = r.id GROUP BY r.name;",
    execution: { terminal: "answered", attempts: [{ passed: true, reason_code: "passed", result_layer: null, path: "agent" }], guardrail_errors: 0 },
    retrieved_tables: ["gbi_demo_sales.customers", "gbi_demo_sales.regions"],
    context_hash: "907e44d8b21c2212f21e01bf79e04737d3bf5305a24aeda98d4b326d54311921",
    corpus_content_hash: "c1296e937e4d7fe0a1b2c3d4e5f60718",
    db_id: "gbi_demo_sales",
    guardrail_errors: 0,
    n_re_served: 0,
  },
  result_table: MOCK_RESULT_TABLE,
};

/* ── A scripted v2 stage-event trajectory (ADR 0010) ──────────────────────────
 *
 * With no backend attached this is the **only** way to see the live timeline, so
 * it is a full turn rather than a sketch, and it is faithful to the emitters in
 * `serve/wrap.py`, `serve/tools.py` and `serve/nodes/finish.py`: the same stage
 * names, the same statuses, and the same closed-vocabulary detail keys — nothing
 * here carries a field the wire does not (ADR 0010 §4 keeps result rows, driver
 * text and nested item bags off the stream, so this fixture has none).
 *
 * The shape it exercises, in order:
 *
 * - the intake rails, including a `rewrite` that fired and the `negative_check`
 * reporting `check: "disabled"` — which is what it reports on essentially every
 * turn today, because `negative_tau` is unset until a negative corpus exists;
 * - the five retrieval channels **interleaved**, starting together and resolving out of order,
 * which is the concurrency the grouped timeline exists to absorb;
 * - `route`/`resolve`/`connect`/`assemble`, with a cross-schema crossing;
 * - the agent loop: two reads, a `check` that governance **blocks**, the repair,
 * then a passing `check` + the `execute` that carries the statement and its
 * digest — the pair that replaced v1's single `run_query` row;
 * - `finish`, the one `final` event, agreeing with the answer fixture's outcome.
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
    detail: { check: "disabled" },
  },

  /* ── the five retrieval channels, concurrent ─────────────────────────────────────────── */
  { seq: 9, id: "channel_schema:turn_mock", kind: "rail", step: "channel_schema", status: "start" },
  { seq: 10, id: "channel_term:turn_mock", kind: "rail", step: "channel_term", status: "start" },
  { seq: 11, id: "channel_metric:turn_mock", kind: "rail", step: "channel_metric", status: "start" },
  { seq: 12, id: "channel_entity:turn_mock", kind: "rail", step: "channel_entity", status: "start" },
  { seq: 13, id: "channel_example:turn_mock", kind: "rail", step: "channel_example", status: "start" },
  { seq: 14, id: "channel_term:turn_mock", kind: "rail", step: "channel_term", status: "ok", detail: { n_hits: 2 } },
  { seq: 15, id: "channel_schema:turn_mock", kind: "rail", step: "channel_schema", status: "ok", detail: { n_hits: 6 } },
  { seq: 16, id: "channel_example:turn_mock", kind: "rail", step: "channel_example", status: "ok", detail: { n_hits: 3 } },
  { seq: 17, id: "channel_metric:turn_mock", kind: "rail", step: "channel_metric", status: "ok", detail: { n_hits: 1 } },
  // Zero hits, and it says so rather than going quiet: a retrieval channel that found nothing
  // is a fact about the corpus.
  { seq: 18, id: "channel_entity:turn_mock", kind: "rail", step: "channel_entity", status: "ok", detail: { n_hits: 0 } },

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
    detail: { n_pulled_in: 4, n_retrieved_tables: 3 },
  },
  { seq: 23, id: "connect:turn_mock", kind: "rail", step: "connect", status: "start" },
  {
    seq: 24,
    id: "connect:turn_mock",
    kind: "rail",
    step: "connect",
    status: "ok",
    // One crossing: table_d lives in `billing` and the rest in `sales`.
    detail: { n_crossings: 1, n_retrieved_tables: 3 },
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
    detail: { attempt: 1, layer: "table", reason_code: "r_table_not_retrieved" },
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
    id: "finish:turn_mock",
    kind: "final",
    step: "finish",
    status: "ok",
    detail: { outcome: "answered", failed_stage: null },
  },
];

export const MOCK_AGENT_ANSWER: AnswerView = {
  outcome: "answered",
  text: null,
  answer_text: "Kansai 15, Great Lakes 15, Rhine-Ruhr 15, Pacific Northwest 15.",
  failed_stage: null,
  error_type: null,
  refused_by: null,
  record: {
    // The register-shaped block first, then this answer's own values, which win.
    ...MOCK_RUN_RECORD,
    outcome: "answered",
    generated_sql: "SELECT r.name AS region, count(c.id) AS customers FROM gbi_demo_sales.customers c JOIN gbi_demo_sales.regions r ON c.region_id = r.id GROUP BY r.name;",
    execution: { terminal: "answered", attempts: [{ passed: true, reason_code: "passed", result_layer: null, path: "agent" }], guardrail_errors: 0 },
    retrieved_tables: ["gbi_demo_sales.customers", "gbi_demo_sales.regions"],
    context_hash: "907e44d8b21c2212f21e01bf79e04737d3bf5305a24aeda98d4b326d54311921",
    corpus_content_hash: "c1296e937e4d7fe0a1b2c3d4e5f60718",
    db_id: "gbi_demo_sales",
    guardrail_errors: 0,
    n_re_served: 0,
  },
  result_table: MOCK_RESULT_TABLE,
};

/* ── Serve-time clarification (HITL) ─────────────────────────────────────────
 * The question the mock agent "interrupts" with when a trigger word is asked
 * (see use-chat.ts CLARIFY_PATTERN). Both a constrained pick AND freeform, to
 * exercise the full prompt (the interrupt payload is ADR 0007 §6). When a backend raises a real
 * `interrupt()`, this fixture is unused. */

export const MOCK_CLARIFICATION: ClarificationRequest = {
  kind: "clarification",
  clarification_id: "clar_mock01",
  question: "Which definition of “active” did you mean?",
  why: "The corpus has two competing definitions of “active” and the question is ambiguous between them.",
  basis: "data_definition",
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
  outcome: "answered",
  text: null,
  answer_text: "Kansai 15, Great Lakes 15, Rhine-Ruhr 15, Pacific Northwest 15.",
  failed_stage: null,
  error_type: null,
  refused_by: null,
  record: {
    // The register-shaped block first, then this answer's own values, which win.
    ...MOCK_RUN_RECORD,
    outcome: "answered",
    generated_sql: "SELECT r.name AS region, count(c.id) AS customers FROM gbi_demo_sales.customers c JOIN gbi_demo_sales.regions r ON c.region_id = r.id GROUP BY r.name;",
    execution: { terminal: "answered", attempts: [{ passed: true, reason_code: "passed", result_layer: null, path: "agent" }], guardrail_errors: 0 },
    retrieved_tables: ["gbi_demo_sales.customers", "gbi_demo_sales.regions"],
    context_hash: "907e44d8b21c2212f21e01bf79e04737d3bf5305a24aeda98d4b326d54311921",
    corpus_content_hash: "c1296e937e4d7fe0a1b2c3d4e5f60718",
    db_id: "gbi_demo_sales",
    guardrail_errors: 0,
    n_re_served: 0,
  },
  result_table: MOCK_RESULT_TABLE,
};

/** Decline / ranking cancel: fail closed, same outcome the live graph finishes. */
export const MOCK_CLARIFICATION_CLOSED: AnswerView = {
  outcome: "clarification",
  text: "The user declined this clarification. The engine did not guess a reading.",
  answer_text: null,
  failed_stage: null,
  error_type: null,
  refused_by: null,
  record: {
    ...MOCK_RUN_RECORD,
    outcome: "clarification",
    generated_sql: null,
    execution: { terminal: "no_sql", attempts: [], guardrail_errors: 0 },
    retrieved_tables: ["gbi_demo_sales.customers", "gbi_demo_sales.regions"],
    context_hash: "907e44d8b21c2212f21e01bf79e04737d3bf5305a24aeda98d4b326d54311921",
    corpus_content_hash: "c1296e937e4d7fe0a1b2c3d4e5f60718",
    db_id: "gbi_demo_sales",
    guardrail_errors: 0,
    n_re_served: 0,
  },
};

/** Questions for an empty conversation. `MOCK_NEARBY_QUESTIONS` is what a refusal offers. */
export const MOCK_EXAMPLE_QUESTIONS: ExampleQuestion[] = [
  { question: "How many customers are in each region?", db_id: "gbi_demo_sales" },
  { question: "What is the total revenue this year?", db_id: "gbi_demo_sales" },
  { question: "Which products sold the most last quarter?", db_id: "gbi_demo_catalog" },
];

export const MOCK_NEARBY_QUESTIONS: ExampleQuestion[] = [
  { question: "How many customers joined last month?", db_id: "gbi_demo_sales" },
  { question: "Which regions have the most orders?", db_id: "gbi_demo_sales" },
  { question: "What is the average order value?", db_id: "gbi_demo_sales" },
];

export const MOCK_REFUSAL: AnswerView = {
  outcome: "refused",
  text: "This question can't be answered as asked: it needs a table this corpus does not license.",
  answer_text: null,
  failed_stage: null,
  error_type: null,
  refused_by: "check",
  record: {
    // The register-shaped block first, then this answer's own values, which win.
    ...MOCK_RUN_RECORD,
    outcome: "refused",
    generated_sql: null,
    execution: { terminal: "refused", attempts: [{ passed: false, reason_code: "r_table_not_retrieved", result_layer: 3, path: "agent" }], guardrail_errors: 0 },
    retrieved_tables: ["gbi_demo_sales.customers", "gbi_demo_sales.regions"],
    context_hash: "907e44d8b21c2212f21e01bf79e04737d3bf5305a24aeda98d4b326d54311921",
    corpus_content_hash: "c1296e937e4d7fe0a1b2c3d4e5f60718",
    db_id: "gbi_demo_sales",
    guardrail_errors: 0,
    n_re_served: 0,
  },
};
