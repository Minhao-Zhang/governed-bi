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

/** One chat surface's resolved identity. Every field nullable: the engine reports what it
 *  resolved, and an offline profile with no model wired resolves none of it. */
const modelSurfaceSchema = z.object({
  id: z.string().nullable(),
  provider: z.string().nullable(),
  effort: z.string().nullable(),
});

/** The embedding surface. `dimensions` is the served width, probed rather than declared. */
const embeddingSurfaceSchema = z.object({
  id: z.string().nullable(),
  provider: z.string().nullable(),
  dimensions: z.number().nullable(),
});

export const capabilitiesSchema = z.object({
  // Free strings on the wire, and neither has an enumerated set to document. There is one
  // deployment profile: `capabilities_for` returns the literal `"local"` for `environment`
  // (no "dev"/"prod" profile exists), and `dialect` is read off the connector, which the
  // served app builds as `PostgresConnector` unconditionally (`api/graph_app.py`). Postgres
  // is the only dialect this engine serves — see `ui/README.md`'s Deployment section.
  environment: z.string(),
  dialect: z.string(),
  // Both constant on a live engine — `false` / `"none"`, because the curator is out of scope
  // of the served surface (ADR 0007 §7). Parsed because they arrive, not because anything
  // renders behind them: `POST /corpus/edit` does not exist and this client mounts no edit
  // affordance.
  can_edit: z.boolean(),
  edit_mode: z.string().nullable(), // "none" on a live engine (backend types it as str | None)
  can_stream: z.boolean(), // LangGraph Server present → useStream, else <NoTransport/>
  has_live_model: z.boolean(),
  model: z.string().nullable(), // null in the offline profile (no model wired)
  // Optional + default false so an engine that omits this still parses. It is read as an
  // observation only: no render path gates on it, because the flat fallback it used to switch
  // to is deleted (see `lib/capabilities.ts` on why `canScope` went with it).
  can_scope: z.boolean().optional().default(false), // scopeable/paginated routes + focus/radius graphs
  // Always false on a live engine: `GET /search` was deliberately never built (ADR 0009
  // Amendment 1), so the client Fuse index is not a fallback — it is the only ranking there is.
  can_search: z.boolean().optional().default(false),
  // Serve-time clarification (HITL): the server can `interrupt()` mid-turn to ask
  // the user one question and resume on the answer. Optional + default false so a server built
  // without HITL degrades cleanly. The prompt itself does **not** gate on this flag — it mounts
  // on the arriving `interrupt()`, so a stale flag cannot hide a question the graph is waiting
  // on (see `components/chat/clarification-prompt.tsx`).
  can_clarify: z.boolean().optional().default(false),
  // The three model surfaces, for /settings. Optional so an engine built before this
  // field still parses — the settings page renders "not reported" rather than breaking.
  //
  // `embedding.id` is **provider-qualified** on the wire (`bedrock:amazon.titan-embed-text-v2:0`)
  // and must be shown as-is: the qualifier is part of the vector cache-key identity, and the id
  // itself can contain a colon (Titan's `…-v2:0`), so splitting it would corrupt the one field
  // that keeps two gateways' vectors apart. `provider` arrives beside it.
  models: z
    .object({
      agent: modelSurfaceSchema,
      utility: modelSurfaceSchema,
      embedding: embeddingSurfaceSchema,
    })
    .optional(),
  // Which warehouse the engine is pointed at, for /settings. Credential-free by construction:
  // the connector redacts, and `user`/`password` are never parsed out of the DSN at all — so
  // there is no field here to accidentally render. `host`/`port` are optional because
  // `connection_for` copies whatever the connector's `endpoint` mapping happens to carry, and a
  // partial session carries none of it — not because a fileless dialect is served here.
  connection: z
    .object({
      dialect: z.string(),
      host: z.string().optional(),
      port: z.string().optional(),
      database: z.string().optional(),
    })
    .optional(),
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
// The producer is `api/routes.py::_knowledge_payload`, whose vocabulary is
// `_SEMANTIC_NODE_KINDS` there. There is no response model to match — the route
// returns a plain dict.
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
// comes from GET /schema/{table_id}; the flat GET /schema dump it used to name is deleted
// (see the note above `assetListSchema` below).
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
 * These were `total_nodes` / `returned_nodes` / `total_edges`, taken from a v1 response model
 * that no longer exists. The engine has always emitted `n_nodes` / `n_edges`, so
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
// Mirrors what `api/routes.py::_graph_payload` emits. Unlike the knowledge
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
  // `no_sql` is the engine's "the turn ended and no governed statement ran" outcome
  // (`register/stages.py::Outcome`). It is listed because `parseAnswer` **drops** an answer whose
  // outcome is not in this enum, so an unlisted member is a turn that renders no card at all.
  outcome: z.enum(["answered", "refused", "clarification", "capped", "crashed", "no_sql"]),
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

/* ── /search — never built ────────────────────────────────────────────────
 *
 * `searchHitSchema` / `searchResponseSchema` are gone, because there is no route to parse.
 * ADR 0009 Amendment 1: "`GET /search` is deliberately **not** built", and
 * `capabilities_for` hardcodes `can_search: false`. Ranking is the client Fuse index over the
 * lean catalog (`lib/catalog.ts`, `lib/asset-catalog.ts`).
 * ────────────────────────────────────────────────────────────────────────── */

// `schemaListSchema` (an array of tableViewSchema, for the flat GET /schema dump) was
// removed with the route. `tableViewSchema` itself stays: GET /schema/{table_id} returns
// exactly one of them, which is the point — a detail is per-item.
export const assetListSchema = z.array(assetRowSchema);

/* ── POST /corpus/edit — never built ──────────────────────────────────────
 *
 * `editResponseSchema` is gone with the client method that posted to it. The route is absent
 * from `docs/openapi.json` and from `src/`, and `capabilities_for` reports `can_edit: false`
 * / `edit_mode: "none"` because the curator is out of scope of the served surface
 * (ADR 0007 §7). Corpus writes are a git/PR job against the corpus repository.
 * ────────────────────────────────────────────────────────────────────────── */

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

/** One open question from `GET /clarifications/pending` — asked, and not yet answered.
 *
 * **Not a turn.** A turn exists once one finishes; these are the ones that never did, so none of
 * `auditTurnSummarySchema`'s outcome fields apply and reusing it would have declared a dozen keys
 * that are structurally absent. The join back is `turn_id`, which the engine parses out of
 * `clarification_id` (`clar-{turn_id}-{digest}`).
 *
 * `turn_id` is nullable on purpose: the engine returns `null` rather than guessing when an id does
 * not have the shape it mints, so a row from some other producer arrives unlinked instead of
 * linked to nowhere. */
export const pendingClarificationSchema = z.object({
  asked_at: z.string().nullable(),
  question: z.string().nullable(),
  why: z.string().nullable(),
  clarification_id: z.string().nullable(),
  turn_id: z.string().nullable(),
  thread_id: z.string().nullable(),
  source: z.enum(["interrupt", "from_refusal", "wrong_answer"]).optional(),
  basis: z.enum(["data_definition", "ranking_ambiguity"]).nullable().optional(),
  // `observation_id` since 2026-08-23, where it was `report_id`: that name was minted by the
  // deleted `serve/raised.py`, and "report" would have been the third meaning of the word in
  // one system. Null on an interrupt row, present on an observation row.
  observation_id: z.string().nullable().optional(),
  /** What a resume would have to name. Carried, not used — this surface is read-only until the
   * corpus write path has a provenance gate. */
  interrupt_id: z.string().nullable().optional(),
  task_id: z.string().nullable().optional(),
});

/** `GET /clarifications/pending`.
 *
 * `meta.truncated` is the field to render, not `n`: a silently short queue reads as "nobody is
 * waiting" and the thing under-reported is a person. `meta.threads_scanned` separates "no open
 * questions" from "the store was not read", which otherwise look identical. */
export const pendingQueueSchema = z.object({
  rows: z.array(pendingClarificationSchema),
  meta: z.object({
    n: z.number(),
    truncated: z.boolean(),
    threads_scanned: z.number(),
    limit: z.number(),
    offset: z.number(),
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
  clarifications: z.array(z.record(z.string(), z.unknown())).optional().default([]),
  // Observations filed about this turn, from `runs/feedback.sqlite`. Was `raised`, which read
  // a checkpoint channel that no longer exists (ADR 0015). Left deliberately opaque, like its
  // `clarifications` sibling: the server owns the row's shape and a client that pinned it
  // would fail the parse for every row in the response the day a field is added.
  observations: z.array(z.record(z.string(), z.unknown())).optional().default([]),
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

/* ── /observations (ADR 0015) ───────────────────────────────────────────────── */

/**
 * One row of the return path's store.
 *
 * `open` is validated as its own boolean although it is derivable from `state`, because the server
 * computes it and a client that recomputed it would be a second answer able to disagree with the
 * first. `question_is_held_out` is carried rather than inferred from `source` for a sharper reason:
 * it is a **warning**, and a warning the client derives is a warning the client can get wrong.
 */
export const observationSchema = z.object({
  observation_id: z.string(),
  filed_at: z.string(),
  source: z.string(),
  kind: z.string(),
  category: z.string().nullable(),
  state: z.string(),
  open: z.boolean(),
  note: z.string(),
  decline_reason: z.string().nullable(),
  duplicate_of: z.string().nullable(),
  blocked_note: z.string(),
  turn_id: z.string().nullable(),
  thread_id: z.string().nullable(),
  question: z.string(),
  outcome: z.string().nullable(),
  refused_by: z.string().nullable(),
  generated_sql: z.string().nullable(),
  licensed: z.array(z.string()),
  schemas: z.array(z.string()),
  missing_tables: z.array(z.string()),
  gold_sql: z.string().nullable(),
  gold_fingerprint: z.string().nullable(),
  pred_fingerprint: z.string().nullable(),
  quality_flags: z.array(z.string()),
  arm: z.string().nullable(),
  question_id: z.string().nullable(),
  db_id: z.string().nullable(),
  corpus_content_hash: z.string().nullable(),
  question_is_held_out: z.boolean(),
  /** Opaque on purpose: the patch shape is not settled, and pinning it here would fail the parse
   * for every row in the response the day a field is added. */
  patches: z.array(z.record(z.string(), z.unknown())).optional().default([]),
  history: z.array(z.record(z.string(), z.unknown())).optional().default([]),
});

export const observationQueueMetaSchema = z.object({
  n: z.number(),
  total: z.number(),
  /** Load-bearing (ADR 0009): a silently short list reads as "this is everything". */
  truncated: z.boolean(),
  limit: z.number(),
  offset: z.number(),
  grouped: z.number().optional(),
});

export const observationsSchema = z.object({
  rows: z.array(observationSchema),
  meta: observationQueueMetaSchema,
});

export const observationClusterSchema = z.object({
  key: z.string(),
  category: z.string().nullable(),
  schema: z.string(),
  n: z.number(),
  /** Whether this is one person hitting a wall repeatedly or several questions blocked by one gap.
   * The two want different amounts of attention and `n` alone cannot tell them apart. */
  n_distinct_questions: z.number(),
  /** The **intersection** across members, not the union: a union grows with the cluster and stops
   * describing what its members share. Empty is a real answer. */
  shared_missing_tables: z.array(z.string()),
  oldest_filed_at: z.string(),
  observations: z.array(observationSchema),
});

export const observationClustersSchema = z.object({
  clusters: z.array(observationClusterSchema),
  meta: observationQueueMetaSchema,
});
