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
  AssumptionRow,
  AuditCorpus,
  AuditTrace,
  AuditTurns,
  Capabilities,
  ClarificationRecord,
  ColumnRelated,
  ConflictRow,
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
  can_curate_corpus: true,
  ui_display_mode: "audit",
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

/* ── /clarifications, POST /clarifications/{id}/answer ───────────────────── */

// Unlike the rest of this file, these five are real content, not abstract
// placeholders — they are the validated Experiment 003 knowledge-category
// fixtures the admin clarification UI was built and manually tested against
// (source-of-truth mapping, business-rule numeric definitions, default
// exclusions, value mapping, join paths), so mock mode still exercises the
// exact copy/shape the feature was designed for.
export const MOCK_CLARIFICATIONS: ClarificationRecord[] = [
  {
    id: "q001",
    scope: "term:revenue",
    question: "When you say 'revenue', which table/column does that map to?",
    status: "open",
    raised_by: ["curator"],
    choices: [
      { id: "c1", label: "payments.amount" },
      { id: "c2", label: "line_items.unit_price" },
      { id: "c3", label: "line_items.unit_price - line_items.discount" },
    ],
    allow_freeform: false,
    answer: null,
    answer_choice_id: null,
    answered_by: null,
    source: "curator",
  },
  {
    id: "q002",
    scope: "rule:fiscal_year_start",
    question: "What month does your fiscal year start?",
    status: "open",
    raised_by: ["curator"],
    choices: [
      { id: "jan", label: "Jan" },
      { id: "apr", label: "Apr" },
      { id: "jul", label: "Jul" },
      { id: "oct", label: "Oct" },
    ],
    allow_freeform: true,
    answer: null,
    answer_choice_id: null,
    answered_by: null,
    source: "curator",
  },
  {
    id: "q003",
    scope: "rule:rating_zero_exclusion",
    question: "Should rating=0 (not yet rated) be excluded from satisfaction averages?",
    status: "open",
    raised_by: ["curator"],
    choices: [
      { id: "yes", label: "Yes" },
      { id: "no", label: "No" },
    ],
    allow_freeform: false,
    answer: null,
    answer_choice_id: null,
    answered_by: null,
    source: "curator",
  },
  {
    id: "q004",
    scope: "term:domestic_country",
    question: "Which country codes count as 'domestic'?",
    status: "open",
    raised_by: ["curator"],
    choices: [
      { id: "us", label: "US" },
      { id: "ca", label: "CA" },
      { id: "mx", label: "MX" },
      { id: "uk", label: "UK" },
    ],
    allow_freeform: true,
    answer: null,
    answer_choice_id: null,
    answered_by: null,
    source: "curator",
  },
  {
    id: "q005",
    scope: "join:category_labels",
    question:
      "To show category names instead of codes, this needs a join to `cat_labels` — confirm?",
    status: "open",
    raised_by: ["curator"],
    choices: [
      { id: "yes", label: "Yes" },
      { id: "no", label: "No" },
      { id: "show_join", label: "Show me the join" },
    ],
    allow_freeform: false,
    answer: null,
    answer_choice_id: null,
    answered_by: null,
    source: "curator",
  },
  {
    id: "q_live_001",
    scope: "live_chat:q_live_001",
    question: "Should refunds count as negative revenue or be excluded entirely?",
    status: "open",
    raised_by: ["live_chat_user"],
    choices: null,
    allow_freeform: true,
    answer: null,
    answer_choice_id: null,
    answered_by: null,
    source: "live_chat",
  },
  // A live question the user handed to the admin rather than answering. Here so mock mode
  // renders the `deferred` badge beside the `open` one above -- the whole point of the status
  // is that the two look different, which a fixture set containing only `open` cannot show.
  {
    id: "q_live_002",
    scope: "live_chat:q_live_002",
    question: "Which of these two customer tables is the one your team treats as current?",
    status: "deferred",
    raised_by: ["live_chat_user"],
    choices: null,
    allow_freeform: true,
    answer: null,
    answer_choice_id: null,
    answered_by: null,
    source: "live_chat",
  },
];

/* ── GET /elicitation/candidates, POST /elicitation/generate (Phase 1c) ───── */

// The proactive admin onboarding wizard's candidates — category-tagged
// (A/C/E/B; D is never standalone, see curator.elicitation on the backend),
// each with the UI modality the design doc specifies for that category, and each
// with the severity/audience the backend's own CATEGORY_CLASSIFICATION assigns it
// (A -> T2/data, B/C/E -> T2/business). Kept in step with that table so the mock
// wizard groups into the same tabs and tiers as the live one.
export const MOCK_ELICITATION_CANDIDATES: ClarificationRecord[] = [
  {
    id: "e001",
    scope: "elicitation:term:revenue",
    question: "When you say 'revenue', which table/column does that map to?",
    status: "open",
    raised_by: ["elicitation_wizard"],
    choices: [
      { id: "payments.amount", label: "payments.amount" },
      { id: "line_items.unit_price", label: "line_items.unit_price" },
    ],
    allow_freeform: false,
    answer: null,
    answer_choice_id: null,
    answer_choice_ids: null,
    answered_by: null,
    source: "elicitation_wizard",
    category: "A",
    ui_modality: "column_picker",
    severity: "T2",
    audience: "data",
    target_table: "line_items",
    target_column: null,
  },
  {
    id: "e002",
    scope: "elicitation:rule:fiscal_year_start",
    question: "What month does your fiscal year start? (enter 1-12, 1 = January)",
    status: "open",
    raised_by: ["elicitation_wizard"],
    choices: null,
    allow_freeform: true,
    answer: null,
    answer_choice_id: null,
    answer_choice_ids: null,
    answered_by: null,
    source: "elicitation_wizard",
    category: "C",
    ui_modality: "numeric",
    severity: "T2",
    audience: "business",
    target_table: null,
    target_column: null,
  },
  {
    id: "e003",
    scope: "elicitation:exclusion:reviews.rating",
    question:
      "Is there a value in `reviews.rating` that means 'not yet rated' (seen: '0')? Should it be excluded from analysis by default?",
    status: "open",
    raised_by: ["elicitation_wizard"],
    choices: [
      { id: "exclude", label: "Exclude rows where rating = '0'" },
      { id: "include", label: "Include them" },
    ],
    allow_freeform: false,
    answer: null,
    answer_choice_id: null,
    answer_choice_ids: null,
    answered_by: null,
    source: "elicitation_wizard",
    category: "E",
    ui_modality: "checkbox",
    severity: "T2",
    audience: "business",
    target_table: "reviews",
    target_column: "rating",
  },
  {
    id: "e004",
    scope: "elicitation:valuemap:customers.country_code",
    question:
      "Which values of `customers.country_code` should count together as 'domestic'? Check all that apply.",
    status: "open",
    raised_by: ["elicitation_wizard"],
    choices: [
      { id: "US", label: "US" },
      { id: "CA", label: "CA" },
      { id: "MX", label: "MX" },
      { id: "UK", label: "UK" },
    ],
    allow_freeform: false,
    answer: null,
    answer_choice_id: null,
    answer_choice_ids: null,
    answered_by: null,
    source: "elicitation_wizard",
    category: "B",
    ui_modality: "checklist",
    severity: "T2",
    audience: "business",
    target_table: "customers",
    target_column: "country_code",
  },
];

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
        id: mockColumnId("table_a", "id"),
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
        id: mockColumnId("table_a", "label"),
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
        id: mockColumnId("table_b", "id"),
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
        id: mockColumnId("table_b", "a_id"),
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
        id: mockColumnId("table_b", "amount"),
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
        id: mockColumnId("table_b", "restricted_field"),
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
        id: mockColumnId("table_c", "a_id"),
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
        id: mockColumnId("table_c", "score"),
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
        id: mockColumnId("table_d", "id"),
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
        id: mockColumnId("table_d", "name"),
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

/* ── /corpus/assumptions — admin "agreed assumptions" log (round 9) ───────── */

export const MOCK_ASSUMPTIONS: AssumptionRow[] = [
  {
    id: "note_sales_1",
    question: "Should refunds count as negative revenue or be excluded?",
    answer: "Exclude refunds from revenue entirely.",
    answered_by: "admin_jane",
    answered_at: "2026-07-20T14:32:00+00:00",
    source: "live_chat",
  },
  {
    id: "note_sales_2",
    question: "What counts as an 'active' customer for churn reporting?",
    answer: "A customer with at least one order in the trailing 90 days.",
    answered_by: "sme",
    answered_at: "2026-07-18T09:05:00+00:00",
    source: "curator",
  },
];

/* ── /corpus/conflicts — Round C: disagreeing definitions, needs review ──── */

export const MOCK_CONFLICTS: ConflictRow[] = [
  {
    id: "note_sales_total_revenue_payments_basis_conflict",
    status: "unresolved",
    existing_asset_id: "metric_sales_net_revenue",
    existing_asset_type: "metric",
    existing_text: "net_revenue = SUM(unit_price - discount) over line_items.",
    existing_question:
      "How should 'revenue' be calculated from line_items? Candidates: unit_price, discount, tax, freight, cogs.",
    new_question:
      "By 'total revenue' do you mean the sum of payments received, or the sum of line-item sales?",
    new_text: "total_revenue_payments_basis = SUM(payments.amount) over payments.",
    answered_by: "admin_jane",
    created_at: "2026-07-21T10:12:00+00:00",
    source: "live_chat",
  },
];


/* ── Chat: a placeholder answer + a refusal, for the mock transport ──────── */

/**
 * The turn record the engine's `stamp` node projects onto EVERY terminal answer. It reaches
 * the client verbatim — nothing is redacted on the way out — so the mock carries it too, or
 * the offline drawer would never exercise the grouping that keeps it from burying the
 * governance fields.
 *
 * **Keys and value shapes are the register's** (`register/record.py`), checked against a real
 * `runs/serve` turn on 2026-08-12; only the values are synthetic. The previous fixture was
 * shaped like v1's deleted run log — 16 of its 21 keys named no register field — so the mock
 * drove a drawer no live turn produces, and after `lib/provenance.ts` was re-derived it would
 * have rendered entirely under "Other".
 *
 * The deliberate `null`s are load-bearing: the register encodes an unmeasured field as null so
 * "not measured" stays distinguishable from "measured zero" (ADR 0005 §6). `guardrail_errors: 0`
 * is the measured zero; `reflect_verdict: null` is the absence.
 *
 * Absent-by-design, matching a real answered turn: `budget_dropped`,
 * `budget_best_dropped_score` and `abstention` are `Absence.not_applicable` and simply do not
 * appear when nothing dropped and the policy is off. A mock that invented them would exercise
 * rows the drawer never shows.
 */
export const MOCK_RUN_RECORD: Record<string, unknown> = {
  /* Tier.identity */
  run_id: "93aacb3752844310",
  turn_id: "272a8b9f34e7fac6",
  thread_id: "019ff347-e719-70b3-809a-ecb1dbfebb16",
  question_id: "c79749267012c5de",
  db_id: "BIRD-corpus",
  attempt_id: "ca9bed0e39d0bb07",

  /* Tier.treatment */
  evicted: null,
  context_hash: "68de872dea5744c98bf0a24eb723396d215590a6ae68abb82989fbb4283e54ff",
  delivery_hash: "f04255af03ff1ce43b05502196def2030e1c5d76ab0108516193535c90b8b299",
  tool_delivered: {},
  corpus_content_hash: "86ed1dbfef8b325e188061229b665c4918ec8c86c65e39b619a5495b0abab6d5",
  prompt_set_hash: "b1f9e4d7d230cb97",
  knobs_resolved: { summary_max_chars: 250, schema_top_n: 3, lexical_weight: 0.5 },

  /* Tier.decision */
  facet_hits: {
    facet_schema: { queries: ["customers per region"], hits: [{ asset_id: "gbi_demo_sales.customers", asset_type: "table", lexical: 2.9, semantic: 0.81 }] },
  },
  facet_degraded: true,
  schema_ranking: [["gbi_demo_sales", 2.9], ["gbi_demo_support", 2.62], ["gbi_demo_hr", 1.14]],
  schemas: ["gbi_demo_sales"],
  pulled_in: { "gbi_demo_sales.customers.region_id": "resolve", "gbi_demo_sales.regions.id": "connect" },
  licensed: ["gbi_demo_sales.customers", "gbi_demo_sales.regions"],
  crossings: [],
  lexical_coverage: 0.8,
  rewrite: null,
  guard: { outcome: "clear", rule_id: null, detail: null },
  negative: { outcome: "disabled", tau: null, top_score: null, matched_id: null },
  execution: { attempts: [], terminal: "no_sql", guardrail_errors: 0 },
  reflect_verdict: null,
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

  /* Tier.health */
  facet_channels: {
    facet_schema: { lexical: "ran", semantic: "ran" },
    facet_entity: { lexical: "ran", semantic: "failed" },
  },
  guardrail_errors: 0,
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
  // Gap 1 (utku-ai-deployment-targets.md): a sample self-reported assumption, and a result
  // table, so the mock transport still exercises the two panels the engine-faithful upstream
  // fixture dropped. Both are real fields on `answerViewSchema`; leaving them out of every
  // mock meant the only way to see either panel was to attach a backend.
  assumptions: ["Excluded rows with a null category before grouping."],
  result_table: {
    columns: ["region", "customers"],
    rows: [
      ["Kansai", 15],
      ["Great Lakes", 15],
      ["Rhine-Ruhr", 15],
      ["Pacific Northwest", 15],
    ],
    row_count: 4,
    truncated: false,
  },
  record: {
    // The full register-shaped block first, so the offline drawer exercises all three named
    // groups; then this answer's own values, which win.
    ...MOCK_RUN_RECORD,
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
  assumptions: [],
  // Synthetic, and shaped like what the v2 engine actually emits -- a mock in the old shape
  // is worse than no mock, because it makes a broken UI look correct.
  outcome: "answered",
  text: null,
  answer_text: "Kansai 15, Great Lakes 15, Rhine-Ruhr 15, Pacific Northwest 15.",
  failed_stage: null,
  error_type: null,
  refused_by: null,
  record: {
    // The full register-shaped block first, so the offline drawer exercises all three named
    // groups; then this answer's own values, which win.
    ...MOCK_RUN_RECORD,
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
  // Gap 1 (utku-ai-deployment-targets.md): a sample self-reported assumption, and a result
  // table, so the mock transport still exercises the two panels the engine-faithful upstream
  // fixture dropped. Both are real fields on `answerViewSchema`; leaving them out of every
  // mock meant the only way to see either panel was to attach a backend.
  assumptions: ["Excluded rows with a null category before grouping."],
  result_table: {
    columns: ["region", "customers"],
    rows: [
      ["Kansai", 15],
      ["Great Lakes", 15],
      ["Rhine-Ruhr", 15],
      ["Pacific Northwest", 15],
    ],
    row_count: 4,
    truncated: false,
  },
  record: {
    // The full register-shaped block first, so the offline drawer exercises all three named
    // groups; then this answer's own values, which win.
    ...MOCK_RUN_RECORD,
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
  assumptions: [],
  record: {
    // The full register-shaped block first, so the offline drawer exercises all three named
    // groups; then this answer's own values, which win.
    ...MOCK_RUN_RECORD,
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
  assumptions: [],
  record: {
    // The full register-shaped block first, so the offline drawer exercises all three named
    // groups; then this answer's own values, which win.
    ...MOCK_RUN_RECORD,
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
