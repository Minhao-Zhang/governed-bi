/**
 * Typed client for the engine's custom REST routes (handoff §4), mounted on the
 * LangGraph Server. Every response is validated with zod at the boundary
 * (fail-loud). Streaming chat goes through `useStream` (see the chat feature);
 * this covers the read routes, the non-streaming `POST /chat` fallback, and the
 * dev `POST /corpus/edit`.
 *
 * In mock mode (`USE_MOCKS`, i.e. no `NEXT_PUBLIC_LANGGRAPH_URL`) each call
 * resolves to a neutral placeholder from `lib/mock/fixtures`, so the UI renders
 * with no backend. With a URL set, the mocks are never touched.
 */

import { z } from "zod";

import { LANGGRAPH_URL, USE_MOCKS } from "@/lib/env";
import {
  MOCK_ANSWER,
  MOCK_ASSETS,
  MOCK_ASSUMPTIONS,
  MOCK_AUDIT_CORPUS,
  MOCK_AUDIT_TRACE,
  MOCK_AUDIT_TURNS,
  MOCK_CAPABILITIES,
  MOCK_CLARIFICATIONS,
  MOCK_CONFLICTS,
  MOCK_ELICITATION_CANDIDATES,
  MOCK_ER_GRAPH,
  MOCK_GRAPH,
  MOCK_REFUSAL,
  MOCK_SCHEMA,
  MOCK_SCHEMA_SUMMARY,
  mockColumnRelated,
} from "@/lib/mock/fixtures";
import {
  applyErGraphScope,
  applyKnowledgeGraphScope,
  filterSummaryItems,
} from "@/lib/graph-scope";
import {
  answerViewSchema,
  assetListSchema,
  assumptionListSchema,
  auditCorpusSchema,
  auditTraceSchema,
  auditTurnsSchema,
  corpusFieldsSchema,
  corpusRowsSchema,
  capabilitiesSchema,
  clarificationListSchema,
  clarificationRecordSchema,
  runtimeToggleListSchema,
  runtimeToggleSchema,
  columnRelatedResponseSchema,
  conflictListSchema,
  conflictResolveResponseSchema,
  draftApprovalSchema,
  draftListSchema,
  editResponseSchema,
  elicitationGenerateResponseSchema,
  erGraphSchema,
  feedbackRecordSchema,
  knowledgeGraphSchema,
  schemaSummaryResponseSchema,
  searchResponseSchema,
  tableViewSchema,
} from "@/lib/schemas";
import type {
  AnswerView,
  AssetRow,
  AssumptionRow,
  AuditCorpus,
  AuditTrace,
  AuditTurns,
  Capabilities,
  CorpusFields,
  CorpusRows,
  CorpusWhere,
  ChatTurn,
  ClarificationRecord,
  RuntimeToggle,
  ColumnRelated,
  ConflictResolveResponse,
  ConflictRow,
  DraftApproval,
  DraftRow,
  EditResponse,
  ElicitationGenerateResponse,
  ErGraph,
  FeedbackRecord,
  KnowledgeGraph,
  SchemaScope,
  SchemaSummaryResponse,
  SearchResponse,
  TableView,
} from "@/lib/types";

/** Questions routed to a refusal in mock mode (mirrors the engine's fail-closed
 * negative-example / excluded-field gates). */
const MOCK_REFUSAL_PATTERN = /restrict|exclud|pii|card|secret|password/i;

/** An empty bucket of a scan report's diff, for the mock generate response. */
const EMPTY_SCAN_BUCKET = { count: 0, by_severity: {}, scopes: [] };

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function parse<T>(path: string, schema: z.ZodType<T>, data: unknown): T {
  const parsed = schema.safeParse(data);
  if (!parsed.success) {
    throw new ApiError(`${path} response did not match the expected schema.`);
  }
  return parsed.data;
}

/** Fetch + zod-parse a live route (mock handled by the caller). */
async function getLive<T>(path: string, schema: z.ZodType<T>): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${LANGGRAPH_URL}${path}`, {
      headers: { accept: "application/json" },
    });
  } catch {
    throw new ApiError(`Could not reach the backend at ${LANGGRAPH_URL}${path}.`);
  }
  if (!res.ok) throw new ApiError(`${path} returned ${res.status}.`, res.status);
  return parse(path, schema, await res.json());
}

async function get<T>(path: string, schema: z.ZodType<T>, mock: T): Promise<T> {
  if (USE_MOCKS) return mock;
  return getLive(path, schema);
}

/** Build a `?a=1&b=2` query string, dropping empty/undefined params. */
function qs(params: Record<string, string | number | undefined | null>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** Map a UI `SchemaScope` onto the engine's query params (``schema`` wire name). */
function scopeQuery(scope?: SchemaScope): string {
  if (!scope) return "";
  return qs({
    schema: scope.schema,
    focus: scope.focus,
    radius: scope.radius,
    node_budget: scope.nodeBudget,
    kinds: scope.kinds?.length ? scope.kinds.join(",") : undefined,
  });
}

async function post<T>(path: string, body: unknown, schema: z.ZodType<T>): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${LANGGRAPH_URL}${path}`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(`Could not reach the backend at ${LANGGRAPH_URL}${path}.`);
  }
  if (!res.ok) throw new ApiError(`${path} returned ${res.status}.`, res.status);
  return parse(path, schema, await res.json());
}

export const api = {
  capabilities: (): Promise<Capabilities> =>
    get("/capabilities", capabilitiesSchema, MOCK_CAPABILITIES),

  // `schema()` — the flat `GET /schema` dump — is **gone**, with the route. It was 936 KB of
  // every table with every column inlined, and a second projection of the tables
  // `schemaSummary()` returns lean. Its consumers now read the lean catalog (which gained the
  // two fields they needed) or `tableDetail()` for the one table someone opens.

  /** Lean, scopeable, paginated catalog (GET /schema/summary; D15, gated on
   * can_scope). Backs the virtualized browser + the search index. */
  schemaSummary: (scope?: {
    schema?: string;
    limit?: number;
    offset?: number;
  }): Promise<SchemaSummaryResponse> => {
    if (USE_MOCKS) return Promise.resolve(filterSummaryItems(MOCK_SCHEMA_SUMMARY.items, scope));
    const query = qs({ schema: scope?.schema, limit: scope?.limit, offset: scope?.offset });
    return getLive(`/schema/summary${query}`, schemaSummaryResponseSchema);
  },

  /** One table's full detail (GET /schema/{id}; D15, gated on can_scope), fetched
   * lazily when a detail sheet opens. `id` is globally unique, so no compound key. */
  tableDetail: (id: string): Promise<TableView> => {
    if (USE_MOCKS) {
      const table = MOCK_SCHEMA.find((t) => t.id === id);
      if (!table) return Promise.reject(new ApiError(`/schema/${id} not found.`, 404));
      return Promise.resolve(table);
    }
    return getLive(`/schema/${encodeURIComponent(id)}`, tableViewSchema);
  },

  /** Server-ranked search (GET /search; D15 DEFERRED, gated on can_search). The
   * default path is the client Fuse index (see lib/catalog.ts); this is only
   * called when capabilities.can_search is true. */
  search: (q: string): Promise<SearchResponse> => {
    if (USE_MOCKS) {
      const needle = q.trim().toLowerCase();
      const hits = MOCK_SCHEMA_SUMMARY.items
        .filter((t) => t.physical_name.toLowerCase().includes(needle))
        .map((t) => ({
          kind: "table",
          id: t.id,
          table_id: t.id,
          label: t.physical_name,
          schema: t.schema,
          detail: null,
          excluded: t.excluded,
          has_suspect: t.has_suspect,
          score: 1,
        }));
      return Promise.resolve({ query: q, total: hits.length, hits });
    }
    return getLive(`/search${qs({ q })}`, searchResponseSchema);
  },

  /** The full knowledge graph over all asset types (GET /knowledge-graph).
   * Optional D15 scope (schema/focus/radius/node_budget/kinds) returns a bounded
   * neighborhood + boundary/meta envelope; no scope = today's full graph. */
  knowledgeGraph: (scope?: SchemaScope): Promise<KnowledgeGraph> => {
    if (USE_MOCKS) return Promise.resolve(applyKnowledgeGraphScope(MOCK_GRAPH, scope));
    return getLive(`/knowledge-graph${scopeQuery(scope)}`, knowledgeGraphSchema);
  },

  /** The ER tables+joins graph (GET /graph): FK edges with cardinality + the
   * join predicate. Combined with /schema columns to draw the ER diagram.
   * Accepts the same optional D15 scope as knowledgeGraph. */
  erGraph: (scope?: SchemaScope): Promise<ErGraph> => {
    if (USE_MOCKS) return Promise.resolve(applyErGraphScope(MOCK_ER_GRAPH, scope));
    return getLive(`/graph${scopeQuery(scope)}`, erGraphSchema);
  },

  assets: (type?: string): Promise<AssetRow[]> =>
    get(
      type ? `/corpus/assets?type=${encodeURIComponent(type)}` : "/corpus/assets",
      assetListSchema,
      type ? MOCK_ASSETS.filter((a) => a.asset_type === type) : MOCK_ASSETS,
    ),

  /** Admin-answered clarifications folded into the corpus (GET /corpus/assumptions;
   * round 9) — the "agreed assumptions" log, distinct from the raw asset editor. */
  assumptions: (): Promise<AssumptionRow[]> =>
    get("/corpus/assumptions", assumptionListSchema, MOCK_ASSUMPTIONS),

  /** Round C: clarifications whose Enhancer decision CONTRADICTED an existing
   * asset (GET /corpus/conflicts), distinct from the settled assumptions log.
   * Param-less returns both unresolved and resolved conflicts. */
  conflicts: (status?: string): Promise<ConflictRow[]> =>
    get(
      `/corpus/conflicts${qs({ status })}`,
      conflictListSchema,
      status ? MOCK_CONFLICTS.filter((c) => c.status === status) : MOCK_CONFLICTS,
    ),

  /** Resolve one conflict (POST /corpus/conflicts/{id}/resolve; dev, gated on
   * can_edit). `resolution` is "keep_existing" (discard the new answer) or
   * "replace" (overwrite the existing asset's definition with it). */
  resolveConflict: (
    id: string,
    resolution: "keep_existing" | "replace",
  ): Promise<ConflictResolveResponse> => {
    if (USE_MOCKS) {
      const status = resolution === "replace" ? "resolved_replaced" : "resolved_kept_existing";
      return Promise.resolve({
        resolved: true,
        conflict_id: id,
        status,
        detail: `ok: resolved ${id} (${resolution})`,
      });
    }
    return post(
      `/corpus/conflicts/${encodeURIComponent(id)}/resolve`,
      { resolution, answered_by: "admin" },
      conflictResolveResponseSchema,
    );
  },

  /** The approval queue (GET /corpus/drafts; fix round, task D) -- every `proposed` asset,
   * read fresh off disk on every call. Not `/corpus/assets` filtered client-side (D-2's
   * original choice): that route reads `session.assets_by_id`, a run constant frozen at
   * session-build time, so it never observed an approval within one server process -- a hard
   * refresh brought an approved draft back into the queue. No mock fixture carries a
   * `proposed` row (mirrors `assets()`'s own mock data), so mock mode renders this tab's
   * empty state, same as before this fix. */
  drafts: (): Promise<DraftRow[]> => get("/corpus/drafts", draftListSchema, []),

  /** Certify one `proposed` draft (POST /corpus/drafts/{id}/approve; task D -- the trust
   * loop's approval terminus). Not gated on `can_edit` -- mirrors `resolveConflict`'s and
   * `answerClarification`'s pattern exactly: the route only requires `session.corpus_root`,
   * which `capabilities.can_curate_corpus` reports. `by` is optional and, when set, recorded
   * in the asset's `audit.extra` -- never required. */
  approveDraft: (id: string, by?: string): Promise<DraftApproval> => {
    if (USE_MOCKS) {
      const asset = MOCK_ASSETS.find((a) => a.id === id);
      return Promise.resolve({
        id,
        asset_type: asset?.asset_type ?? "term",
        provenance_status: "certified",
      });
    }
    return post(
      `/corpus/drafts/${encodeURIComponent(id)}/approve`,
      by ? { by } : {},
      draftApprovalSchema,
    );
  },

  /** Every semantic-layer item touching one physical column (GET
   * /columns/{column_id}/related; handoff §14). `columnId` is the derived id from
   * the engine's column asset id, READ from a column payload — never derived client-side
   * (ADR 0008 D1/D4). Joins are resolved server-side; metrics are table-grain. */
  columnRelated: (columnId: string): Promise<ColumnRelated> => {
    if (USE_MOCKS) return Promise.resolve(mockColumnRelated(columnId));
    return getLive(
      `/columns/${encodeURIComponent(columnId)}/related`,
      columnRelatedResponseSchema,
    );
  },

  /** Non-streaming one-shot answer (POST /chat) — the fallback when the backend
   * reports `can_stream: false`. Streaming chat uses `useStream` instead. */
  chat: (question: string, history: ChatTurn[], sessionId: string): Promise<AnswerView> => {
    if (USE_MOCKS) {
      if (MOCK_REFUSAL_PATTERN.test(question)) return Promise.resolve(MOCK_REFUSAL);
      return Promise.resolve(MOCK_ANSWER);
    }
    return post(
      "/chat",
      { question, session_id: sessionId, history },
      answerViewSchema,
    );
  },

  /** Validate + write a corpus asset (POST /corpus/edit; dev, gated on can_edit). */
  edit: (asset: Record<string, unknown>): Promise<EditResponse> => {
    if (USE_MOCKS) {
      return Promise.resolve({
        written: false,
        asset_id: String(asset.id ?? "unknown"),
        asset_type: String(asset.asset_type ?? "unknown"),
        path: null,
        findings: ["Editing requires a connected dev backend."],
        diff: "",
      });
    }
    return post("/corpus/edit", { asset }, editResponseSchema);
  },

  /** SME clarification ledger (GET /clarifications) for the admin to answer.
   * `status` matches one exact value; param-less returns every record. */
  clarifications: (status?: string): Promise<ClarificationRecord[]> =>
    get(
      `/clarifications${qs({ status })}`,
      clarificationListSchema,
      status
        ? MOCK_CLARIFICATIONS.filter((c) => c.status === status)
        : MOCK_CLARIFICATIONS,
    ),

  /** Answer one clarification (POST /clarifications/{id}/answer; dev, gated on
   * can_edit). One of `choiceId`/`choiceIds`/`answer` must be set — `choiceIds`
   * is the elicitation wizard's category-B multi-select checklist; every other
   * category/source uses `choiceId` and/or `answer`, unchanged. */
  answerClarification: (
    id: string,
    body: { choiceId?: string; choiceIds?: string[]; answer?: string },
  ): Promise<ClarificationRecord> => {
    if (USE_MOCKS) {
      const record =
        MOCK_CLARIFICATIONS.find((c) => c.id === id) ??
        MOCK_ELICITATION_CANDIDATES.find((c) => c.id === id);
      if (!record) return Promise.reject(new ApiError(`/clarifications/${id}/answer not found.`, 404));
      return Promise.resolve({
        ...record,
        status: "answered",
        answer: body.answer ?? null,
        answer_choice_id: body.choiceId ?? null,
        answer_choice_ids: body.choiceIds ?? null,
        answered_by: "admin",
      });
    }
    return post(
      `/clarifications/${encodeURIComponent(id)}/answer`,
      { choice_id: body.choiceId, choice_ids: body.choiceIds, answer: body.answer },
      clarificationRecordSchema,
    );
  },

  /** Abandon a pending clarification (POST /clarifications/{id}/cancel).
   *
   * **Not a resume.** The graph thread stays paused and is never answered; this only writes the
   * ledger, and what it writes depends on the record's own `basis` — a `ranking_ambiguity`
   * question lands `cancelled` and leaves the admin queue, anything else stays `open`. The server
   * decides, which is why there is no body: see
   * `docs/utkuai-role-tiers-and-clarification-cancel.md`.
   *
   * Returns the resulting row, so a caller can say which of the two happened without refetching. */
  cancelClarification: (id: string): Promise<ClarificationRecord> => {
    if (USE_MOCKS) {
      const record = MOCK_CLARIFICATIONS.find((c) => c.id === id);
      if (!record) return Promise.reject(new ApiError(`/clarifications/${id}/cancel not found.`, 404));
      return Promise.resolve({
        ...record,
        status: record.basis === "ranking_ambiguity" ? "cancelled" : "open",
      });
    }
    return post(
      `/clarifications/${encodeURIComponent(id)}/cancel`,
      {},
      clarificationRecordSchema,
    );
  },

  /** A reader who was refused submits what they meant (POST /clarifications/from-refusal,
   * task A). Enters the same ledger an `ask_user` interrupt would -- the entrance a
   * `no_schema_matched` refusal structurally cannot reach, since it fires before the agent
   * (and `ask_user`) ever runs. `explanation` becomes the record's own `answer`; see the
   * route's own docstring for why that folds immediately rather than waiting on an admin. */
  fileClarificationFromRefusal: (
    question: string,
    explanation: string,
  ): Promise<ClarificationRecord> => {
    if (USE_MOCKS) {
      const id = `refusal-${Date.now()}`;
      return Promise.resolve({
        id,
        scope: `refusal:${id}`,
        question,
        status: "answered",
        raised_by: [],
        choices: null,
        allow_freeform: true,
        answer: explanation,
        answer_choice_id: null,
        answered_by: "user",
        source: "refusal",
        basis: "data_definition",
        converted_to_corpus: true,
      });
    }
    return post(
      "/clarifications/from-refusal",
      { question, answer: explanation },
      clarificationRecordSchema,
    );
  },

  /** A reader says one turn's *delivered* answer is wrong (POST /feedback, task H-3) -- the
   * reader's other entrance into the semantic layer, for the case where the engine answered and
   * the business knows the answer is wrong. Distinct from `fileClarificationFromRefusal` above,
   * which is for the case where the engine said nothing at all (H-b: two different record types,
   * never merged). `reason` is the reader's optional one-line explanation. */
  fileFeedback: (params: {
    turnId: string;
    question: string;
    answerText: string;
    reason?: string;
  }): Promise<FeedbackRecord> => {
    if (USE_MOCKS) {
      const id = `feedback-${Date.now()}`;
      return Promise.resolve({
        id,
        turn_id: params.turnId,
        question: params.question,
        answer_text: params.answerText,
        status: "open",
        reason: params.reason ?? null,
        reported_at: new Date().toISOString(),
        correction: null,
        answered_by: null,
        converted_to_corpus: false,
      });
    }
    return post(
      "/feedback",
      {
        turn_id: params.turnId,
        question: params.question,
        answer_text: params.answerText,
        reason: params.reason,
      },
      feedbackRecordSchema,
    );
  },

  /** Engine switches an operator may flip, with where each current value came from
   * (GET /settings/toggles). */
  toggles: (): Promise<RuntimeToggle[]> =>
    get("/settings/toggles", runtimeToggleListSchema, []),

  /** Set one switch, or clear it back to its default with `null`
   * (POST /settings/toggles/{name}). Returns the resulting row, so the caller renders what the
   * engine actually resolved rather than what it asked for — the two differ when the environment
   * pins the knob, which is a 409. */
  setToggle: (name: string, value: boolean | null): Promise<RuntimeToggle> => {
    if (USE_MOCKS) {
      return Promise.resolve({
        name, value, source: value === null ? "default" : "override",
        default: false, why: "Mock transport: no engine attached.", editable: true, env_var: null,
      });
    }
    return post(`/settings/toggles/${encodeURIComponent(name)}`, { value }, runtimeToggleSchema);
  },

  /** Phase 1 elicitation wizard candidates — open AND answered
   * ``source="elicitation_wizard"`` ledger records (GET /elicitation/candidates).
   * Answer the same way as any other clarification, via `answerClarification`. */
  elicitationCandidates: (): Promise<ClarificationRecord[]> =>
    get("/elicitation/candidates", clarificationListSchema, MOCK_ELICITATION_CANDIDATES),

  /** Trigger candidate-question generation from the served schema (POST
   * /elicitation/generate; gated on can_curate_corpus, not can_edit -- see
   * api/curation_routes.py's own docstring). Idempotent on the backend —
   * safe to call again; returns only newly proposed candidates, plus `report`,
   * the diff against what the ledger and the corpus already knew. */
  elicitationGenerate: (): Promise<ElicitationGenerateResponse> => {
    if (USE_MOCKS)
      return Promise.resolve({
        generated: [],
        n_generated: 0,
        report: {
          nothing_new: true,
          summary: "No new gaps found.",
          new: EMPTY_SCAN_BUCKET,
          still_open: EMPTY_SCAN_BUCKET,
          settled: EMPTY_SCAN_BUCKET,
          stranded: EMPTY_SCAN_BUCKET,
        },
      });
    return post("/elicitation/generate", {}, elicitationGenerateResponseSchema);
  },


  /* ── the audit surface ─────────────────────────────────────────────────── */
  //
  // Ungated: these are projections of the turn log and the loaded corpus, so they
  // need no model and no capability flag. They are also the only way to see a turn
  // again — `POST /chat` returns the record inline, once, to the caller who asked.

  /** Served turns, newest first (GET /audit/turns). `threadId` narrows to one conversation.
   *
   * **Why a transcript needs this.** A turn's record lives in per-turn graph state, which the
   * engine clears every turn (`PER_TURN_RESET`), so `values.answer` describes the newest turn
   * and nothing earlier. This log is the only place every turn of a conversation survives. */
  auditTurns: (limit = 50, threadId?: string): Promise<AuditTurns> =>
    get(`/audit/turns${qs({ limit, thread_id: threadId })}`, auditTurnsSchema, MOCK_AUDIT_TURNS),

  /** One turn, grouped by the pipeline stage that produced each recorded field
   * (GET /audit/turns/{id}/trace). The grouping is derived from the engine's
   * record register, so a new field appears here with no change to this app. */
  auditTrace: (turnId: string): Promise<AuditTrace> =>
    get(
      `/audit/turns/${encodeURIComponent(turnId)}/trace`,
      auditTraceSchema,
      MOCK_AUDIT_TRACE,
    ),

  /** Corpus shape plus its problems, split fatal vs degradation (GET /audit/corpus). */
  auditCorpus: (): Promise<AuditCorpus> =>
    get("/audit/corpus", auditCorpusSchema, MOCK_AUDIT_CORPUS),

  /* ── filtering (ADR 0009) ──────────────────────────────────────────────── */

  /** The filterable columns of one asset type, derived server-side from the asset
   * dataclass + register. The filter row is generated from this, so a field added to the
   * engine becomes filterable with no change here. */
  corpusFields: (type: string): Promise<CorpusFields> =>
    get(`/corpus/fields${qs({ type })}`, corpusFieldsSchema, {
      type,
      columns: [],
      types: [],
      detail: "No backend is attached.",
    }),

  /** Filtered, sorted, paginated assets of one type.
   *
   * `where` is serialised as repeated `field:op:value`. The value is **not** escaped and
   * does not need to be: the server splits on the first two colons only, so a value may
   * contain them — which is required, because asset ids are dotted and some contain colons. */
  corpusRows: (params: {
    type: string;
    where?: CorpusWhere[];
    sort?: string;
    order?: "asc" | "desc";
    offset?: number;
    limit?: number;
  }): Promise<CorpusRows> => {
    if (USE_MOCKS) {
      return Promise.resolve({
        rows: [],
        total: 0,
        offset: 0,
        limit: params.limit ?? 50,
        columns: [],
        unknown_where: [],
      });
    }
    const query = qs({
      type: params.type,
      sort: params.sort,
      order: params.order,
      offset: params.offset,
      limit: params.limit,
    });
    const clauses = (params.where ?? [])
      .filter((w) => w.field && w.op)
      .map((w) => `where=${encodeURIComponent(`${w.field}:${w.op}:${w.value}`)}`);
    const joined = clauses.length ? `${query ? "&" : "?"}${clauses.join("&")}` : "";
    return getLive(`/corpus/rows${query}${joined}`, corpusRowsSchema);
  },
};
