/**
 * Typed client for the engine's custom REST routes, mounted on the LangGraph
 * Server. The mounted surface is enumerated in `../docs/openapi.json` — the
 * inventory of record — and decided in ADR 0009 Amendment 1; the capability
 * flags that gate it are ADR 0007 §7. Every response is validated with zod at
 * the boundary (fail-loud). Streaming chat goes through `useStream` (see the
 * chat feature); this covers the fourteen reads and the one write.
 *
 * In mock mode (`USE_MOCKS`, i.e. no `NEXT_PUBLIC_LANGGRAPH_URL`) each call
 * resolves to a neutral placeholder from `lib/mock/fixtures`, so the UI renders
 * with no backend. With a URL set, the mocks are never touched.
 */

import { z } from "zod";

import { LANGGRAPH_URL, USE_MOCKS } from "@/lib/env";
import {
  MOCK_ASSETS,
  MOCK_AUDIT_CORPUS,
  MOCK_AUDIT_TRACE,
  MOCK_AUDIT_TURNS,
  MOCK_OBSERVATIONS,
  MOCK_PATCH,
  MOCK_PATCHES,
  MOCK_OBSERVATION_CLUSTERS,
  MOCK_PENDING_QUEUE,
  MOCK_CAPABILITIES,
  MOCK_ER_GRAPH,
  MOCK_GRAPH,
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
  assetListSchema,
  auditCorpusSchema,
  auditTraceSchema,
  auditTurnsSchema,
  observationSchema,
  observationClustersSchema,
  observationEnvelopeSchema,
  observationsSchema,
  patchEnvelopeSchema,
  patchesSchema,
  pendingQueueSchema,
  corpusFieldsSchema,
  corpusRowsSchema,
  capabilitiesSchema,
  columnRelatedResponseSchema,
  erGraphSchema,
  knowledgeGraphSchema,
  schemaSummaryResponseSchema,
  tableViewSchema,
} from "@/lib/schemas";
import type {
  Observation,
  ObservationClusters,
  ObservationEnvelope,
  Observations,
  PatchEnvelope,
  Patches,
  AssetRow,
  AuditCorpus,
  AuditTrace,
  AuditTurns,
  PendingQueue,
  Capabilities,
  CorpusFields,
  CorpusRows,
  CorpusWhere,
  ColumnRelated,
  ErGraph,
  KnowledgeGraph,
  SchemaScope,
  SchemaSummaryResponse,
  TableView,
} from "@/lib/types";

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

/**
 * `PATCH`, for the one route that has one.
 *
 * A separate function rather than a `method` argument on `post`, because the two differ in what a
 * non-2xx *means* and the caller has to be able to tell: a 409 from `PATCH /observations/{id}` is
 * "somebody has already triaged this, so the note is frozen", which is a sentence to show, not a
 * failure to retry. `ApiError` carries the status for exactly that.
 */
async function patch<T>(path: string, body: unknown, schema: z.ZodType<T>): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${LANGGRAPH_URL}${path}`, {
      method: "PATCH",
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

  // `search()` — a client for `GET /search` — is **gone**, and the route it called never
  // existed: ADR 0009 Amendment 1 says it is "deliberately **not** built", and
  // `api/routes.py::capabilities_for` hardcodes `can_search: false`, so the branch could not
  // be reached against any engine. Ranking is the Fuse index over the lean catalog
  // (`lib/catalog.ts` for tables, `lib/asset-catalog.ts` for every asset kind) — not a
  // fallback, the only one there is.

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
   * /columns/{column_id}/related; ADR 0009 Amendment 1). `columnId` is the derived id from
   * the engine's column asset id, READ from a column payload — never derived client-side
   * (ADR 0008 D1/D4). Joins are resolved server-side; metrics are table-grain. */
  columnRelated: (columnId: string): Promise<ColumnRelated> => {
    if (USE_MOCKS) return Promise.resolve(mockColumnRelated(columnId));
    return getLive(
      `/columns/${encodeURIComponent(columnId)}/related`,
      columnRelatedResponseSchema,
    );
  },

  // `chat()` — the non-streaming `POST /chat` one-shot — is **gone**, with the route. It was
  // the transport for a backend reporting `can_stream: false`, and it answered on a different
  // checkpointer from the streaming graph, so a question replayed through it arrived with none
  // of the conversation that preceded it. Chat is `useStream` and nothing else; an engine that
  // cannot stream now says so (see <ChatPanel/>) instead of being answered badly.

  // `edit()` — a client for `POST /corpus/edit` — is **gone**, and that route never existed
  // either. `capabilities_for` reports `can_edit: false` with `edit_mode: "none"` because the
  // curator is out of scope of the served surface (ADR 0007 §7); writing the corpus is a
  // git/PR job against the corpus repository, not a request from this client. The only write
  // this app makes is `raiseTurn` below.

  /* ── the audit surface ─────────────────────────────────────────────────── */
  //
  // Ungated: these are projections of the turn log and the loaded corpus, so they
  // need no model and no capability flag. They are how a turn is seen *across* threads
  // and after the checkpoint's TTL sweeps it — the log outlives the conversation.

  /** Served turns, newest first (GET /audit/turns) — **across every thread**.
   *
   * The `thread_id` filter this used to take is gone with its only caller. The chat
   * transcript no longer reads the log at all: each finished turn's envelope accumulates on
   * the graph's own `turns` channel, so a conversation's records come from the same store as
   * its messages (see `lib/stream-messages.turnAnswersByMessageId`). What is left here is the
   * cross-thread audit list, which is what `/audit` renders and what the route is for. */
  auditTurns: (limit = 50): Promise<AuditTurns> =>
    get(`/audit/turns${qs({ limit })}`, auditTurnsSchema, MOCK_AUDIT_TURNS),

  /** Questions the engine asked that nobody has answered, oldest first
   * (GET /clarifications/pending).
   *
   * Cross-thread, which is the whole point: `useStream` surfaces the interrupt on the
   * conversation you are looking at, and this is every *other* one — a reader who closed the tab
   * leaves no other trace, because the engine records a clarification only on the far side of
   * `interrupt()`.
   *
   * Read `meta.truncated` before presenting `meta.n` as a count. */
  pendingClarifications: (limit = 50, offset = 0): Promise<PendingQueue> =>
    get(
      `/clarifications/pending${qs({ limit, offset })}`,
      pendingQueueSchema,
      MOCK_PENDING_QUEUE,
    ),

  /**
   * The review queue (`GET /observations`), **oldest first**.
   *
   * Oldest-first because the row that has waited longest is the one to act on — the opposite of
   * `/audit`, where the newest event is the one you came for. Read `meta.truncated` before
   * presenting `meta.total` as a count.
   */
  observations: (params: {
    state?: string;
    category?: string;
    limit?: number;
    offset?: number;
  } = {}): Promise<Observations> =>
    get(
      `/observations${qs({
        state: params.state,
        category: params.category,
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
      })}`,
      observationsSchema,
      MOCK_OBSERVATIONS,
    ),

  /**
   * The same queue, grouped structurally (`GET /observations?group=cluster`).
   *
   * The grouping is `(category, schema)` and nothing more — no embedding, no model, no cost — so
   * the surface that renders it must carry the caption saying nothing read the questions. Measured
   * on the 73 failures imported from the v4 arm: 37 of 54 clusters hold one observation and the
   * largest holds three, so this helps about half the queue and no more.
   */
  observationClusters: (limit = 200): Promise<ObservationClusters> =>
    get(
      `/observations${qs({ group: "cluster", state: "open,triaged", limit })}`,
      observationClustersSchema,
      MOCK_OBSERVATION_CLUSTERS,
    ),

  /** One observation, its drafted patches, and its transition history. */
  observation: (observationId: string): Promise<Observation> =>
    get(
      `/observations/${encodeURIComponent(observationId)}`,
      observationSchema,
      MOCK_OBSERVATIONS.rows[0],
    ),

  /* ── the steward's verbs ────────────────────────────────────────────────
   *
   * **All four 404 unless the engine was started with `GOVERNED_BI_FEEDBACK_ADMIN` set** — a 403
   * would confirm the routes exist. That means a 404 from any of them is ambiguous between "no
   * such row" and "this engine does not offer the verb", and the surface says so rather than
   * guessing; there is no capability flag for it, because `/capabilities` describes the serve
   * path.
   *
   * **None of them writes to the corpus.** Drafting records what a change would be. The write is
   * `git apply` and a commit in the corpus repository, run by a person, and that gap is the
   * provenance gate the whole return path is built around.
   */

  /** Move an observation. The server's transition table decides; a 409 means it refused. */
  triageObservation: (
    observationId: string,
    body: {
      to: string;
      detail?: string;
      decline_reason?: string;
      duplicate_of?: string;
      blocked_note?: string;
    },
  ): Promise<ObservationEnvelope> => {
    if (USE_MOCKS) {
      return Promise.resolve({
        ok: true,
        observation: { ...MOCK_OBSERVATIONS.rows[0], state: body.to, open: body.to !== "declined" },
      });
    }
    return post(
      `/observations/${encodeURIComponent(observationId)}/triage`,
      body,
      observationEnvelopeSchema,
    );
  },

  /** Amend the note on an untriaged observation. 409 once somebody has looked. */
  amendObservation: (
    observationId: string,
    body: { note?: string; expected?: string },
  ): Promise<ObservationEnvelope> => {
    if (USE_MOCKS) {
      return Promise.resolve({
        ok: true,
        observation: { ...MOCK_OBSERVATIONS.rows[0], note: body.note ?? "" },
      });
    }
    return patch(
      `/observations/${encodeURIComponent(observationId)}`,
      body,
      observationEnvelopeSchema,
    );
  },

  /**
   * Draft a patch (`POST /patches`, 201).
   *
   * `base_corpus_content_hash` must be the **full** 64-character digest. The 16-character prefix
   * every display shows is refused with 422, because it never equals the digest the landing check
   * compares against — a patch nobody had touched would report `superseded`.
   */
  draftPatch: (body: {
    intent: string;
    namespace: string;
    asset_type?: string;
    asset_id?: string;
    field_path?: string;
    was?: string;
    becomes?: string;
    rationale?: string;
    base_corpus_content_hash: string;
    observations?: string[];
  }): Promise<PatchEnvelope> => {
    if (USE_MOCKS) return Promise.resolve({ ok: true, patch: MOCK_PATCH });
    return post("/patches", body, patchEnvelopeSchema);
  },

  /** Abandon a patch. The reason is required by the server, not just by the form. */
  withdrawPatch: (patchId: string, reason: string): Promise<PatchEnvelope> => {
    if (USE_MOCKS) {
      return Promise.resolve({
        ok: true,
        patch: { ...MOCK_PATCH, state: "withdrawn", withdrawn_reason: reason },
      });
    }
    return post(
      `/patches/${encodeURIComponent(patchId)}/withdraw`,
      { reason },
      patchEnvelopeSchema,
    );
  },

  /** Patches, newest first. `state` is comma-separated. */
  patches: (params: { state?: string; limit?: number } = {}): Promise<Patches> =>
    get(
      `/patches${qs({ state: params.state, limit: params.limit ?? 50 })}`,
      patchesSchema,
      MOCK_PATCHES,
    ),

  /**
   * File an observation about a finished turn (`POST /turns/{id}/raised`).
   *
   * 201, not 200: the route creates a row in a store rather than appending to a channel.
   * `category` and `expected` are optional -- the first tap files something valid, and a
   * refinement is never a gate.
   */
  raiseTurn: (
    turnId: string,
    body: {
      kind: "from_refusal" | "wrong_answer";
      category?: string;
      note?: string;
      expected?: string;
    },
  ): Promise<{ ok: boolean; observation: Record<string, unknown> }> => {
    if (USE_MOCKS) {
      return Promise.resolve({
        ok: true,
        observation: { kind: body.kind, note: body.note ?? "", state: "open", open: true },
      });
    }
    return post(
      `/turns/${encodeURIComponent(turnId)}/raised`,
      body,
      z.object({ ok: z.boolean(), observation: z.record(z.string(), z.unknown()) }),
    );
  },

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
