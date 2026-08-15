/**
 * TypeScript types for the engine contract, inferred from the zod schemas in
 * `lib/schemas.ts` (one source of truth). Import these anywhere in the app;
 * import the schemas only where you parse a network response.
 */

import type { z } from "zod";
import type {
  runtimeToggleSchema,
  answerViewSchema,
  assetRowSchema,
  assumptionRowSchema,
  auditCorpusSchema,
  auditLedgerRowSchema,
  auditTraceFieldSchema,
  auditTraceSchema,
  auditTraceStageSchema,
  auditTurnSummarySchema,
  auditTurnsSchema,
  corpusFieldSchema,
  corpusFieldsSchema,
  corpusRowsSchema,
  boundaryEdgeSchema,
  capabilitiesSchema,
  clarificationRecordSchema,
  columnRelatedResponseSchema,
  columnViewSchema,
  conflictResolveResponseSchema,
  conflictRowSchema,
  editResponseSchema,
  elicitationGenerateResponseSchema,
  erGraphEdgeSchema,
  erGraphNodeSchema,
  erGraphSchema,
  graphEdgeSchema,
  graphMetaSchema,
  graphNodeKindSchema,
  graphNodeSchema,
  knowledgeGraphSchema,
  leanColumnSchema,
  resultTableSchema,
  scanReportSchema,
  schemaSummaryResponseSchema,
  searchHitSchema,
  searchResponseSchema,
  serveOutcomeSchema,
  tableSummarySchema,
  tableViewSchema,
} from "./schemas";

export type ServeOutcome = z.infer<typeof serveOutcomeSchema>;

/* ── the audit surface (GET /audit/*) ────────────────────────────────────── */

export type AuditTurnSummary = z.infer<typeof auditTurnSummarySchema>;
export type AuditTurns = z.infer<typeof auditTurnsSchema>;
export type AuditTraceField = z.infer<typeof auditTraceFieldSchema>;
export type AuditTraceStage = z.infer<typeof auditTraceStageSchema>;
export type AuditLedgerRow = z.infer<typeof auditLedgerRowSchema>;
export type AuditTrace = z.infer<typeof auditTraceSchema>;
export type AuditCorpus = z.infer<typeof auditCorpusSchema>;

/* ── filtering (GET /corpus/fields + /corpus/rows) ───────────────────────── */

export type CorpusField = z.infer<typeof corpusFieldSchema>;
export type CorpusFields = z.infer<typeof corpusFieldsSchema>;
export type CorpusRows = z.infer<typeof corpusRowsSchema>;
/** One `where=field:op:value` predicate, before it is serialised. */
export interface CorpusWhere {
  field: string;
  op: string;
  value: string;
}

export type Capabilities = z.infer<typeof capabilitiesSchema>;
export type ColumnView = z.infer<typeof columnViewSchema>;
export type TableView = z.infer<typeof tableViewSchema>;
export type GraphNodeKind = z.infer<typeof graphNodeKindSchema>;
export type GraphNode = z.infer<typeof graphNodeSchema>;
export type GraphEdge = z.infer<typeof graphEdgeSchema>;
export type KnowledgeGraph = z.infer<typeof knowledgeGraphSchema>;
export type ErGraphNode = z.infer<typeof erGraphNodeSchema>;
export type ErGraphEdge = z.infer<typeof erGraphEdgeSchema>;
export type ErGraph = z.infer<typeof erGraphSchema>;
export type AssetRow = z.infer<typeof assetRowSchema>;
export type AssumptionRow = z.infer<typeof assumptionRowSchema>;
export type ConflictRow = z.infer<typeof conflictRowSchema>;
export type ConflictResolveResponse = z.infer<typeof conflictResolveResponseSchema>;
export type RuntimeToggle = z.infer<typeof runtimeToggleSchema>;
export type ColumnRelated = z.infer<typeof columnRelatedResponseSchema>;
export type ResultTable = z.infer<typeof resultTableSchema>;
export type AnswerView = z.infer<typeof answerViewSchema>;
export type EditResponse = z.infer<typeof editResponseSchema>;
export type { ClarificationChoice, ClarificationRequest, ClarificationResponse } from "@/lib/clarification";
export type ClarificationRecord = z.infer<typeof clarificationRecordSchema>;

/* ── Phase 1 elicitation wizard (proactive admin onboarding) ──────────────── */
export type ElicitationGenerateResponse = z.infer<typeof elicitationGenerateResponseSchema>;
export type ScanReport = z.infer<typeof scanReportSchema>;
export type ElicitationCategory = NonNullable<ClarificationRecord["category"]>;
export type ElicitationUiModality = NonNullable<ClarificationRecord["ui_modality"]>;
export type ElicitationSeverity = NonNullable<ClarificationRecord["severity"]>;
export type ElicitationAudience = NonNullable<ClarificationRecord["audience"]>;

/* ── serve-time HITL (hitl-clarification-contract.md §3/§4/§9) ────────────── */

/** The `interrupt()` value the `ask_user` tool raises; `stream.interrupt.value`. */

/* ── D15 scope-on-demand (gated on capabilities.can_scope / can_search) ───── */
export type LeanColumn = z.infer<typeof leanColumnSchema>;
export type TableSummary = z.infer<typeof tableSummarySchema>;
export type SchemaSummaryResponse = z.infer<typeof schemaSummaryResponseSchema>;
export type BoundaryEdge = z.infer<typeof boundaryEdgeSchema>;
export type GraphMeta = z.infer<typeof graphMetaSchema>;
export type SearchHit = z.infer<typeof searchHitSchema>;
export type SearchResponse = z.infer<typeof searchResponseSchema>;

/**
 * Normalized catalog row for the search omnibox + schema rail. Produced
 * client-side from either the full `/schema` dump or the lean `/schema/summary`
 * (both expose wire ``schema`` → ``namespace`` here), so the rail/search are
 * source-agnostic.
 */
export interface CatalogItem {
  id: string;
  physical_name: string;
  namespace: string;
  row_count: number | null;
  n_columns: number;
  excluded: boolean;
  has_suspect: boolean;
  provenance_status: string | null;
}

/**
 * The scope the Schema tab drives its views by. Empty `{}` = whole corpus
 * (today's flat behavior / fallback). `schema` narrows to one namespace;
 * `focus`+`radius`+`nodeBudget` bound a graph to a table's neighborhood.
 */
export interface SchemaScope {
  schema?: string;
  focus?: string;
  radius?: number;
  nodeBudget?: number;
  kinds?: string[]; // knowledge-graph node-kind filter
}

/**
 * A node selected in either graph, lifted to the page and passed to the detail
 * sheet. `node` carries the full knowledge-graph node when available (for the
 * non-table generic detail); ER selections omit it (always a table → lazy detail).
 */
export interface GraphSelection {
  id: string;
  kind: string;
  label: string;
  node?: GraphNode;
}

/** One prior turn sent to the non-streaming POST /chat (TurnIn). */
export interface ChatTurn {
  role: "user" | "assistant";
  text: string;
}

/** Non-table corpus asset types — the values `/corpus/assets?type=` accepts. */
export const ASSET_TYPES = [
  "metric",
  "term",
  "join",
  "note",
  "few_shot",
  "negative_example",
] as const;
export type AssetType = (typeof ASSET_TYPES)[number];

/**
 * Every corpus asset type, tables included — what the Corpus browser lists.
 *
 * Kept separate from `ASSET_TYPES` on purpose: `table` is a corpus asset in the
 * domain model and is counted as one by `/health`, but `/corpus/assets?type=table`
 * is **not** a valid request (that endpoint serves the non-table assets, and
 * tables come from `/schema`). Folding them into one constant would invite sending
 * `type=table` to an endpoint that rejects it.
 */
export const CORPUS_ASSET_TYPES = ["table", ...ASSET_TYPES] as const;
export type CorpusAssetType = (typeof CORPUS_ASSET_TYPES)[number];
