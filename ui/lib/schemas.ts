/**
 * Zod schemas for every custom-route response — the fail-loud boundary between
 * the UI and the engine. The route set is fixed by engine ADR 0009 Amendment 1,
 * and the answer/stream shapes by engine ADR 0007; `npm run check:api` fetches
 * every route from a live engine and validates it against these.
 *
 * TypeScript types are inferred from these schemas (see `lib/types.ts`) — one
 * source of truth.
 *
 * Namespace wire name is ``schema`` only. The engine does not emit or accept
 * ``db`` for namespace filtering or response fields.
 */

import { z } from "zod";

/* ── /capabilities ───────────────────────────────────────────────────────── */

export const capabilitiesSchema = z.object({
  environment: z.string(), // "dev" | "prod"
  dialect: z.string(), // "sqlite" | "postgres" | "redshift"
  can_edit: z.boolean(),
  edit_mode: z.string().nullable(), // "file" | "pr" | null (backend types it as str | None)
  can_stream: z.boolean(), // LangGraph Server present → useStream, else /chat fallback
  has_live_model: z.boolean(),
  model: z.string().nullable(), // null in the offline profile (no model wired)
  // D15 scope-on-demand flags. Optional + default false so a pre-D15 engine that
  // omits them still parses and the UI falls back to today's flat behavior.
  can_scope: z.boolean().optional().default(false), // scopeable/paginated routes + focus/radius graphs
  can_search: z.boolean().optional().default(false), // server GET /search (else client Fuse index)
  // Serve-time clarification (HITL): the server can `interrupt()` mid-turn to ask
  // the user one question and resume on the answer. Optional + default false so a
  // server built without HITL (or the offline/REST profile) degrades cleanly —
  // the interrupt-prompt UI only mounts when this is true (contract §8).
  can_clarify: z.boolean().optional().default(false),
});

/* ── /health — deleted ────────────────────────────────────────────────────
 *
 * `corpusHealthSchema` is gone with the route (ADR 0007 Amendment 1). `auditCorpusSchema`
 * below covers everything it declared except three counters the engine hardcoded to zero,
 * and it keeps `fatal` apart from `degradations` where this flattened both into `findings`.
 * ────────────────────────────────────────────────────────────────────────── */

/* ── /schema (tables + columns) ──────────────────────────────────────────── */

export const columnViewSchema = z.object({
  // The engine's asset id. Sent so nobody derives one: ADR 0008 D1 mints
  // `{table_id}.{slug(physical_name)}`, and `slug` hashes any name needing sanitisation,
  // so a second implementation in TypeScript would be a second answer to what identifies
  // a column. Optional only so the mock transport can omit it.
  id: z.string().optional(),
  // Facts (read-only)
  physical_name: z.string(),
  physical_type: z.string(),
  logical_type: z.string(),
  nullable: z.boolean(),
  is_unique: z.boolean(),
  sample_values: z.array(z.unknown()).default([]),
  // Inference (editable)
  description: z.string().nullable().optional(),
  role: z.string().nullable().optional(),
  references: z.string().nullable().optional(),
  confidence: z.number().nullable().optional(),
  // Governance + reliability + audit
  reliability: z.string().default("ok"), // "ok" | "suspect"
  reliability_note: z.string().nullable().optional(),
  excluded: z.boolean().default(false),
  excluded_reason: z.string().nullable().optional(),
  provenance_status: z.string().nullable().optional(),
  evidence: z.string().nullable().optional(),
});

export const tableViewSchema = z.object({
  id: z.string(),
  physical_name: z.string(),
  schema: z.string(),
  row_count: z.number().nullable(),
  description: z.string().nullable(),
  grain: z.string().nullable(),
  confidence: z.number().nullable(),
  excluded: z.boolean(),
  excluded_reason: z.string().nullable(),
  provenance_status: z.string().nullable(),
  columns: z.array(columnViewSchema),
});

/* ── /schema/summary — lean, scopeable catalog (D15, gated on can_scope) ──── */
// Lean projection for the virtualized browser + client search index: drops the
// heavy per-column fields (sample_values/evidence/description).

export const leanColumnSchema = z.object({
  id: z.string().optional(), // the engine's asset id; never derived client-side (ADR 0008 D4)
  physical_name: z.string(),
  physical_type: z.string(),
  role: z.string().nullable().optional(),
  reliability: z.string().default("ok"),
  excluded: z.boolean().default(false),
  // Tri-state on purpose: `null` is "not observed", which an ER card must render
  // differently from a measured `false`. These two are what let the diagram read the
  // lean catalog instead of the 937 KB flat `/schema` dump.
  nullable: z.boolean().nullable().optional(),
  is_unique: z.boolean().nullable().optional(),
});

export const tableSummarySchema = z.object({
  id: z.string(),
  physical_name: z.string(),
  schema: z.string(),
  row_count: z.number().nullable(),
  n_columns: z.number(),
  excluded: z.boolean(),
  has_suspect: z.boolean(),
  provenance_status: z.string().nullable(),
  columns: z.array(leanColumnSchema).default([]),
});

export const schemaSummaryResponseSchema = z.object({
  total: z.number(),
  // The page **as applied** after the server's clamp — declared so a short page is
  // attributable. `total: 656` with 200 items cannot otherwise be told apart from the end
  // of the list, and that ambiguity hid 456 tables from the rail and the search index.
  offset: z.number().optional(),
  limit: z.number().optional(),
  items: z.array(tableSummarySchema),
});

/* ── /graph (full knowledge graph over all asset types) ──────────────────── */

// Node kinds the backend emits (= asset_type): tables + the non-table assets.
// Matches KnowledgeGraphNodeResponse.kind (governed_bi.api.schemas).
export const graphNodeKindSchema = z.enum([
  "table",
  "join",
  "metric",
  "term",
  "note",
  "few_shot",
  "negative_example",
]);

// The full knowledge-graph node is lean (GET /knowledge-graph): no physical_name/
// row_count/n_columns/summary — those live on the ER GET /graph. Rich table detail
// comes from GET /schema.
export const graphNodeSchema = z.object({
  id: z.string(),
  kind: graphNodeKindSchema,
  label: z.string(),
  excluded: z.boolean(),
  provenance_status: z.string().nullable(),
  confidence: z.number().nullable().optional(),
  has_suspect: z.boolean().optional(),
  // D15: namespace additive + nullable; non-table nodes omit it.
  schema: z.string().nullable().optional(),
});

export const graphEdgeSchema = z.object({
  id: z.string(),
  source: z.string(),
  target: z.string(),
  // Open vocab: join | measures | grounds | related:<rel> | scopes | exemplifies
  // (`related:<rel>` has a dynamic suffix, so this is a string, not an enum).
  relation: z.string(),
  confidence: z.number().nullable().optional(),
  low_confidence: z.boolean().optional(),
});

/* ── Scope-on-demand envelope (D15): boundary + meta for scoped graphs ────── */

/** A curated cross-schema join whose other endpoint is outside the current
 * scope. D15 Q7: cross-schema joins execute, so this renders as a NAVIGABLE
 * boundary stub (click to re-scope onto the other endpoint), never a warning. */
export const boundaryEdgeSchema = z.object({
  id: z.string(),
  in_scope_table: z.string(),
  other_schema: z.string(),
  other_table_id: z.string(),
  other_label: z.string(),
  on: z.string(), // equality predicate
  cardinality: z.string().nullable().optional(),
  confidence: z.number().nullable().optional(),
  low_confidence: z.boolean().optional().default(false),
});

export const graphScopeSchema = z.object({
  schema: z.string().nullable().optional(),
  focus: z.string().nullable().optional(),
  radius: z.number().nullable().optional(),
  node_budget: z.number().nullable().optional(),
  kinds: z.array(z.string()).nullable().optional(),
});

/** `/graph` + `/knowledge-graph` meta. **Names follow the engine (ADR 0009 D2).**
 *
 * These were `total_nodes` / `returned_nodes` / `total_edges`, taken from v1's deleted
 * `governed_bi.api.schemas`. The engine has always emitted `n_nodes` / `n_edges`, so
 * `z.object` was stripping every field and defaulting `truncated` to `false` — the UI could
 * not have shown a truncated graph even once the server started bounding them. Aligned to
 * the engine because ADR 0009 is now the spec for this route and the old names describe a
 * module that no longer exists.
 *
 * `truncated` / `dropped` are the load-bearing pair: a diagram that quietly renders 120 of
 * 656 nodes reads as complete coverage. Anything consuming this must render them. */
export const graphMetaSchema = z.object({
  n_nodes: z.number(),
  n_edges: z.number(),
  n_total_nodes: z.number().optional(),
  /** Nodes matching the scope *before* the budget was applied. */
  n_matched_nodes: z.number().optional(),
  truncated: z.boolean().optional().default(false),
  dropped: z.number().optional().default(0),
  node_budget: z.number().optional(),
  scope: graphScopeSchema.optional().nullable(),
});

// `boundary` + `meta` are optional so a pre-D15 bare {nodes,edges} still parses.
// Live engine may send explicit `null` (not omit) when unscoped — accept nullish.
export const knowledgeGraphSchema = z.object({
  nodes: z.array(graphNodeSchema),
  edges: z.array(graphEdgeSchema),
  boundary: z.array(boundaryEdgeSchema).nullish(),
  meta: graphMetaSchema.nullish(),
});

/* ── /graph (ER: tables + joins, with FK cardinality + predicate) ─────────── */
// Mirrors SchemaGraphNode/Edge (governed_bi.api.schemas). Unlike the knowledge
// graph, ER edges carry the join equality (`on`) and `cardinality`, which powers
// the column-level ER diagram (combined with per-column detail from /schema).

export const erGraphNodeSchema = z.object({
  id: z.string(),
  physical_name: z.string(),
  row_count: z.number().nullable(),
  n_columns: z.number(),
  excluded: z.boolean(),
  has_suspect: z.boolean(),
  // D15: schema namespace (additive + nullable).
  schema: z.string().nullable().optional(),
});

export const erGraphEdgeSchema = z.object({
  id: z.string(),
  source: z.string(),
  target: z.string(),
  on: z.string(), // equality predicate, e.g. "table_b.a_id = table_a.id"
  cardinality: z.string().nullable(), // e.g. "many_to_one"
  confidence: z.number().nullable(),
  low_confidence: z.boolean(),
  // One drawn line can stand for SEVERAL declared relationships between the same
  // table pair — the normal case, and the reason join ids carry an ON digest. The
  // engine sends both; these were undeclared, so zod stripped them and the diagram
  // showed a single `on` with no hint that others existed. Optional because a mock
  // or an older engine may omit them.
  join_ids: z.array(z.string()).optional(),
  n_relationships: z.number().optional(),
});

export const erGraphSchema = z.object({
  nodes: z.array(erGraphNodeSchema),
  edges: z.array(erGraphEdgeSchema),
  boundary: z.array(boundaryEdgeSchema).nullish(),
  meta: graphMetaSchema.nullish(),
});

/* ── /corpus/assets ──────────────────────────────────────────────────────── */

export const assetRowSchema = z.object({
  id: z.string(),
  asset_type: z.string(),
  summary: z.string(),
  provenance_status: z.string().nullable(),
  excluded: z.boolean(),
  // The namespace, which the engine sends and this was discarding — so the asset browser
  // rebuilt it by joining against the catalog to filter by a field it already had. Nullable:
  // a term or a metric belongs to no single namespace, and that is different from unknown.
  schema: z.string().nullable().optional(),
});

/* ── /columns/{column_id}/related (engine ADR 0009) ──────────────────────────
 * Every semantic-layer item that touches one physical column. `column_id` is the
 * engine's column asset id `{table_id}.{slug(physical_name)}` (ADR 0008 D1), taken
 * from a column payload rather than derived here. Joins are resolved server-side
 * from the physical ON predicate; metrics are table-grain only. Nullable/defaulted
 * where the contract allows so a lean payload still parses. */

const columnRefSchema = z.object({
  column_id: z.string(),
  table_id: z.string(),
  physical_name: z.string(),
});

export const columnRelatedResponseSchema = z.object({
  column: z.object({
    id: z.string(),
    table_id: z.string(),
    table_physical_name: z.string(),
    schema: z.string().nullable().optional(),
    physical_name: z.string(),
  }),
  terms: z
    .array(
      z.object({
        id: z.string(),
        name: z.string(),
        synonyms: z.array(z.string()).default([]),
        confidence: z.number().nullable().optional(),
        provenance_status: z.string().nullable().optional(),
      }),
    )
    .default([]),
  rules: z
    .array(
      z.object({
        id: z.string(),
        kind: z.string(),
        statement: z.string(),
        confidence: z.number().nullable().optional(),
        provenance_status: z.string().nullable().optional(),
      }),
    )
    .default([]),
  fk_out: columnRefSchema.nullable().default(null),
  fk_in: z.array(columnRefSchema).default([]),
  joins: z
    .array(
      z.object({
        id: z.string(),
        left_table: z.string(),
        right_table: z.string(),
        other_table_id: z.string(),
        on: z.string(),
        cardinality: z.string().nullable().optional(),
        confidence: z.number().nullable().optional(),
        low_confidence: z.boolean().optional().default(false),
      }),
    )
    .default([]),
  metrics: z
    .array(
      z.object({
        id: z.string(),
        name: z.string(),
        granularity: z.string().default("table"),
      }),
    )
    .default([]),
  meta: z.object({ column_resolvable: z.boolean() }).optional(),
});

/* ── Answer (chat terminal state) ────────────────────────────────────────── */

export const resultTableSchema = z.object({
  columns: z.array(z.string()),
  rows: z.array(z.array(z.unknown())),
  row_count: z.number(),
  truncated: z.boolean(),
});

/**
 * The engine's answer, as v2 actually emits it (engine ADR 0007 §3).
 *
 * **`tier`, `safety_clearance` and `semantic_assurance` are gone and must not come back
 * as defaults.** None of them exists in the v2 engine — the reliability-tier concept was
 * deliberately not carried across the rewrite. Defaulting `tier` to `"governed"` here
 * would put a reliability claim with nothing behind it on the most prominent badge in the
 * interface, which is the class of defect the rewrite existed to remove. If a component
 * cannot render without one, the badge goes, not the honesty.
 *
 * `text` and `answer_text` are **different fields on purpose**: `text` is what the
 * *system* says (refusal and decline copy, null on the answered path) and `answer_text` is
 * what the *model* said. Do not fall back from one to the other — a refusal has `text` set
 * and `answer_text` null, and that distinction is the signal.
 *
 * `record` is the engine's 37-key projection over its `RECORD_REGISTER`. Left as an open
 * record rather than enumerated: the register is the authority, and a hand-copied field
 * list here would drift the first time one is added.
 */
export const answerViewSchema = z.object({
  outcome: z.enum(["answered", "refused", "clarification", "capped", "crashed"]),
  text: z.string().nullable(),
  answer_text: z.string().nullable().optional(),
  failed_stage: z.string().nullable().optional(),
  error_type: z.string().nullable().optional(),
  refused_by: z.string().nullable().optional(),
  record: z.record(z.string(), z.unknown()).default({}),
  // Whether this turn reached the durable turn log, and why not if it did not. The engine
  // sends both and they were undeclared, so zod stripped them: a silently-discarded "your
  // turn was not recorded" is the precise loss the turn log exists to prevent, because the
  // answer still renders and only the audit trail is missing. Optional — an engine without a
  // log says nothing rather than claiming success.
  audit_logged: z.boolean().optional(),
  audit_error: z.string().nullable().optional(),
});

/* ── /search — server-ranked search (D15, DEFERRED; gated on can_search) ──── */
// Q6: server FTS stays deferred; the default is a client Fuse index over the
// summary catalog. This shape is the parse target only when can_search is true.
export const searchHitSchema = z.object({
  kind: z.string(), // "table" | "column" | asset kind
  id: z.string(),
  table_id: z.string().nullable().optional(),
  label: z.string(),
  schema: z.string().nullable(),
  detail: z.string().nullable().optional(),
  excluded: z.boolean().optional().default(false),
  has_suspect: z.boolean().optional().default(false),
  score: z.number().optional(),
});

export const searchResponseSchema = z.object({
  query: z.string(),
  total: z.number(),
  hits: z.array(searchHitSchema),
});

// `schemaListSchema` (an array of tableViewSchema, for the flat GET /schema dump) was
// removed with the route. `tableViewSchema` itself stays: GET /schema/{table_id} returns
// exactly one of them, which is the point — a detail is per-item.
export const assetListSchema = z.array(assetRowSchema);

/* ── POST /corpus/edit (dev only; gated on capabilities.can_edit) ─────────── */

/** Response from writing/validating a corpus asset (EditResponse). */
export const editResponseSchema = z.object({
  written: z.boolean(), // false when validation blocked the write
  asset_id: z.string(),
  asset_type: z.string(),
  path: z.string().nullable(), // repo-relative path written (null when not written)
  findings: z.array(z.string()), // reference-integrity findings (empty = clean)
  diff: z.string(), // unified diff of the YAML file
});

/* ── GET /audit/* — the trace and audit surface ───────────────────────────── */
//
// Everything is under `/audit` because `GET /runs` returns 405 on this server:
// LangGraph Server owns `POST /runs`, so a route named for what it holds would have
// collided with the platform's own.
//
// Field names mirror the engine's record register (`register/record.py`) rather than
// being renamed for display. A UI name for a recorded field is a second spelling of a
// declared fact, and the engine's own docs are then no longer a reference for this app.

/** One row of `GET /audit/turns` — a served turn, summarised. */
export const auditTurnSummarySchema = z.object({
  turn_id: z.string().nullable(),
  run_id: z.string().nullable(),
  thread_id: z.string().nullable(),
  question_id: z.string().nullable(),
  db_id: z.string().nullable(),
  outcome: z.string().nullable(),
  terminal_reason: z.string().nullable(),
  schemas: z.array(z.string()).nullable(),
  generated_sql: z.string().nullable(),
  // `cost_est_usd` is gone with the engine's price table. It was declared here and was `null`
  // on every served turn, because nothing on the serve path ever priced one — the only caller of
  // `estimate_run_cost` was the eval driver. A column that is always null is a column a reader
  // learns to distrust. Token counts stay in the record's `usage` rows, and the provider prices
  // them.
  latency_sec: z.number().nullable(),
  asked_at: z.string().nullable(),
  question: z.string().nullable(),
  answer_text: z.string().nullable(),
  licensed_count: z.number(),
  attempts: z.number(),
  // The attempt ledger, passed through so a transcript rebuilt from this log carries the same
  // governance badge the live turn showed. Undeclared it was stripped by zod — the engine sent
  // it and the card still read "no SQL attempted" above its own SQL panel, which is exactly the
  // silent-strip the `audit_logged` comment below records happening once before.
  execution: z.record(z.string(), z.unknown()).nullable().optional(),
  attempts_passed: z.number(),
  /** How many *required* register fields the record is missing. Non-zero means the
   * turn is not quotable — a turn whose record is incomplete is not a turn that worked. */
  incomplete_fields: z.number(),
});

export const auditTurnsSchema = z.object({
  turns: z.array(auditTurnSummarySchema),
  meta: z.object({
    n: z.number(),
    log_dir: z.string(),
    columns: z.array(z.string()),
  }),
});

/** One recorded field inside a stage section of the trace.
 *
 * **`tier` here is not a reliability tier** and is not the forbidden answer-card field.
 * It is `RecordField.tier` off the engine's `RECORD_REGISTER` — *why a field is recorded*:
 * `identity` | `treatment` | `decision` | `outcome` | `cost` | `health`. The engine
 * serialises it in `api/routes.py`'s `audit_trace` as `field.tier.value`, so it is live on
 * the wire. It says how a reader may use a recorded field, never how much to trust an
 * answer. */
export const auditTraceFieldSchema = z.object({
  name: z.string(),
  tier: z.string(),
  value: z.unknown(),
  present: z.boolean(),
  required_and_absent: z.boolean(),
  why: z.string(),
});

/** One stage of the pipeline, with the fields the register says it owns. */
export const auditTraceStageSchema = z.object({
  stage: z.string(),
  fields: z.array(auditTraceFieldSchema),
});

/** One governed execution attempt, from the ledger. */
export const auditLedgerRowSchema = z
  .object({
    passed: z.boolean().nullable().optional(),
    reason_code: z.string().nullable().optional(),
    verdict_layer: z.string().nullable().optional(),
    detail: z.string().nullable().optional(),
    sql_hash: z.string().nullable().optional(),
    path: z.string().nullable().optional(),
  })
  .passthrough();

export const auditTraceSchema = z.object({
  found: z.boolean(),
  turn_id: z.string(),
  question: z.string().nullable().optional(),
  answer_text: z.string().nullable().optional(),
  outcome: z.string().nullable().optional(),
  asked_at: z.string().nullable().optional(),
  stages: z.array(auditTraceStageSchema).optional().default([]),
  ledger: z.array(auditLedgerRowSchema).optional().default([]),
  terminal: z.string().nullable().optional(),
  missing_required: z.array(z.string()).optional().default([]),
  // Folded in from the deleted `GET /audit/turns/{turn_id}`, which nothing called.
  //
  // `stages` is the *register's* view of the record, so it can only show fields the register
  // declares. `record` is the record itself, and `undeclared_keys` names what is in it that
  // nothing declared — the one signal that a producer has started writing a field no one has
  // taught the register about. A stage list looks complete either way, which is exactly why
  // this has to be its own key rather than an inference.
  record: z.record(z.string(), z.unknown()).optional(),
  undeclared_keys: z.array(z.string()).optional().default([]),
});

/** `GET /audit/corpus` — what the corpus is, and what is wrong with it.
 *
 * `fatal` and `degradations` are separate lists rather than one with a flag, because
 * ADR 0008 D9 makes them different states: fatal means an id is not a key and the
 * corpus is not what it claims; a degradation means the corpus is smaller than the
 * lake. Blurring them would put the CLI and the server back into disagreement. */
export const auditCorpusSchema = z.object({
  corpus_content_hash: z.string().nullable(),
  assets: z.object({
    total: z.number(),
    by_type: z.record(z.string(), z.number()),
  }),
  schemas: z.array(z.string()),
  structure: z.object({
    join_edges: z.number(),
    references: z.number(),
    schema_tags: z.number(),
    untagged_assets: z.number(),
    table_pairs_with_joins: z.number(),
  }),
  problems: z.object({
    fatal: z.array(z.string()),
    degradations: z.array(z.string()),
    n_fatal: z.number(),
    n_degradations: z.number(),
  }),
  servable: z.boolean(),
});

/* ── GET /corpus/fields + /corpus/rows — filtering with derived columns ────── */
//
// ADR 0009 D1. The column list is **derived server-side** from the asset dataclass plus the
// asset register, so the filter row is generated rather than written here: a field added to
// the engine's `corpus/schema.py` becomes filterable with no change to this app. That is the
// whole reason these two routes exist as a pair instead of one endpoint with fixed columns.

/** One filterable column, as the engine describes it. */
export const corpusFieldSchema = z.object({
  name: z.string(),
  /** Decides which control the filter row renders. */
  kind: z.enum(["string", "number", "boolean", "enum", "ref", "list", "block"]),
  /** The operators this column accepts. The UI offers exactly these — offering one the
   * server does not accept would put the predicate in `unknown_where` instead of applying
   * it, which looks like a filter that did nothing. */
  ops: z.array(z.string()),
  sortable: z.boolean(),
  /** The register marks this as the type's identifier: what a reader searches by. */
  identifier: z.boolean(),
});

export const corpusFieldsSchema = z.object({
  type: z.string().nullable(),
  columns: z.array(corpusFieldSchema),
  types: z.array(z.string()),
  detail: z.string().nullable().optional(),
});

export const corpusRowsSchema = z.object({
  /** Rows are flat and JSON-safe; nested blocks arrive as rendered text. */
  rows: z.array(z.record(z.string(), z.unknown())),
  /** Count **after** filtering and before pagination. */
  total: z.number(),
  offset: z.number(),
  limit: z.number(),
  columns: z.array(corpusFieldSchema),
  /** Predicates the server could not apply. Must be shown: a dropped filter renders a
   * filtered-looking list that is not filtered. */
  unknown_where: z.array(z.string()),
  detail: z.string().nullable().optional(),
});
