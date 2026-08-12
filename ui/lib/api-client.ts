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

import { authHeaders, LANGGRAPH_URL, USE_MOCKS } from "@/lib/env";
import {
  MOCK_ANSWER,
  MOCK_ASSETS,
  MOCK_AUDIT_CORPUS,
  MOCK_AUDIT_TRACE,
  MOCK_AUDIT_TURNS,
  MOCK_CAPABILITIES,
  MOCK_ER_GRAPH,
  MOCK_GRADED_ANSWER,
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
  auditCorpusSchema,
  auditTraceSchema,
  auditTurnsSchema,
  corpusFieldsSchema,
  corpusRowsSchema,
  capabilitiesSchema,
  columnRelatedResponseSchema,
  editResponseSchema,
  erGraphSchema,
  knowledgeGraphSchema,
  schemaSummaryResponseSchema,
  searchResponseSchema,
  tableViewSchema,
} from "@/lib/schemas";
import type {
  AnswerView,
  AssetRow,
  AuditCorpus,
  AuditTrace,
  AuditTurns,
  Capabilities,
  CorpusFields,
  CorpusRows,
  CorpusWhere,
  ChatTurn,
  ColumnRelated,
  EditResponse,
  ErGraph,
  KnowledgeGraph,
  SchemaScope,
  SchemaSummaryResponse,
  SearchResponse,
  TableView,
} from "@/lib/types";

/** Questions routed to a refusal in mock mode (mirrors the engine's fail-closed
 * negative-example / excluded-field gates). */
const MOCK_REFUSAL_PATTERN = /restrict|exclud|pii|card|secret|password/i;
const MOCK_GRADED_PATTERN = /graded|unverified|fenced/i;

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
      headers: { accept: "application/json", ...authHeaders() },
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
        ...authHeaders(),
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
      if (MOCK_GRADED_PATTERN.test(question)) return Promise.resolve(MOCK_GRADED_ANSWER);
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
