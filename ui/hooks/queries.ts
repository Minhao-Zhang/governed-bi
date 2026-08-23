/**
 * React Query hooks over the custom-route client. Consumers are Client
 * Components. Param-less keys stay stable so the whole app shares one cache;
 * scoped keys embed the scope so each D15 scope caches independently.
 *
 * **No hook here is capability-gated.** They were, and the fallbacks they gated are gone:
 * `GET /schema` is deleted, and `can_scope === false` (which is also what an unresolved
 * `/capabilities` looks like) sent the graphs out unscoped and straight into the client's
 * alphabetical truncation — 150 nodes, 0 edges. `useServerSearch` went the same way: it gated
 * `GET /search`, a route ADR 0009 Amendment 1 says was deliberately never built, so the hook
 * was permanently disabled and the Fuse index in `lib/catalog.ts` is the whole search story.
 */

"use client";

import { useMemo } from "react";
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "@/lib/api-client";
import {
  buildCatalogIndex,
  searchCatalog,
  summaryToCatalog,
} from "@/lib/catalog";
import { USE_MOCKS } from "@/lib/env";
import { listConversations } from "@/lib/threads";
import type {
  CatalogItem,
  CorpusWhere,
  SchemaScope,
  TableView,
} from "@/lib/types";

export function useCapabilities() {
  return useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities });
}

/**
 * The table catalog. **The only one** — `useSchema`, over the flat `GET /schema` dump, is
 * gone along with the route.
 *
 * That dump was 936 KB of every table with every column inlined, and a second projection of
 * the same tables this returns lean: two shapes for one thing, which drifted, and because the
 * dump was also the fallback catalog source one wrong field emptied the namespace rail too.
 * Its last real consumer was the ER diagram, which needed two fields (`nullable`,
 * `is_unique`) now carried on the lean column.
 *
 * No longer gated on `can_scope`: that flag says whether the engine honours *scope
 * parameters*, not whether the catalog exists. Gating the route on it meant an engine
 * reporting `can_scope: false` had no catalog at all once the dump was deleted — a capability
 * flag deciding whether a page can render is a flag doing something it does not describe.
 */
export function useSchemaSummary(
  scope?: SchemaScope,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ["schema-summary", scope?.schema ?? null],
    queryFn: () => api.schemaSummary({ schema: scope?.schema }),
    enabled: options?.enabled ?? true,
    // Do NOT keepPreviousData: client filters stale items by the new namespace and
    // would flash an empty catalog while the next summary loads.
  });
}

/**
 * One table's full detail, resolved lazily when a detail sheet opens: `GET /schema/{id}`.
 *
 * There used to be a second path that fetched the whole 936 KB dump and searched it for one
 * table. It existed because the per-table route was gated on `can_scope`; it is not, and
 * fetching a corpus to read one row of it was never the cheaper branch.
 */
export function useTableDetail(id: string | null) {
  return useQuery({
    queryKey: ["table-detail", id],
    enabled: id !== null,
    queryFn: (): Promise<TableView> => api.tableDetail(id!),
  });
}

/**
 * The semantic graph (GET /knowledge-graph) and the ER graph (GET /graph), both scoped.
 *
 * **The `can_scope` gate is gone, and dropping it is a bug fix.** Both hooks used to send
 * `scoped ? scope : undefined`, and `canScope(caps)` is false whenever `caps` is *undefined* —
 * which is every first render, before `/capabilities` resolves. So the first fetch of each
 * graph went out with no scope at all; the engine applied its own default budget and echoed
 * it; that echo could not equal the budget the component filters with; `engineScopeMatches`
 * failed; and the client fell back to truncating alphabetically, which returns nodes with
 * almost no edges between them. Measured this morning on the unscoped path: 150 nodes, **0
 * edges**. `keepPreviousData` then holds that render until the scoped refetch lands.
 *
 * The flag also no longer gates anything real: the engine honours a scope parameter whether or
 * not it reports `can_scope`, and `/schema/summary` was ungated for the same reason. A
 * capability flag whose false branch is a known-broken path is worse than no flag.
 */
export function useKnowledgeGraph(scope?: SchemaScope) {
  return useQuery({
    queryKey: ["knowledge-graph", scope ?? null],
    queryFn: () => api.knowledgeGraph(scope),
    placeholderData: keepPreviousData,
  });
}

export function useErGraph(scope?: SchemaScope) {
  return useQuery({
    queryKey: ["er-graph", scope ?? null],
    queryFn: () => api.erGraph(scope),
    placeholderData: keepPreviousData,
  });
}

/**
 * The normalized catalog behind the search omnibox + schema rail — from the lean
 * `/schema/summary`, which is now the only table catalog. The `can_scope` branch that
 * projected the flat dump is gone with the dump.
 */
export function useCatalog(scope?: SchemaScope) {
  const summary = useSchemaSummary(scope);
  // Primitive dep — callers often pass a fresh `{}` / scope object each render.
  const schemaFilter = scope?.schema;

  const items = useMemo<CatalogItem[]>(() => {
    const raw = summary.data ? summaryToCatalog(summary.data.items) : [];
    // Belt-and-suspenders: keep the catalog aligned with the active namespace even
    // if a live summary response ignored the wire `schema` filter.
    if (schemaFilter) return raw.filter((it) => it.namespace === schemaFilter);
    return raw;
  }, [summary.data, schemaFilter]);

  return {
    items,
    isLoading: summary.isLoading,
    isError: summary.isError,
  };
}

/** Client-side fuzzy search over a catalog (the default; permanent at these
 * sizes per D15 Q6). Synchronous + memoized so the index isn't rebuilt per key. */
export function useCatalogSearch(
  items: CatalogItem[],
  query: string,
): CatalogItem[] {
  const index = useMemo(() => buildCatalogIndex(items), [items]);
  return useMemo(
    () => searchCatalog(index, items, query),
    [index, items, query],
  );
}

// `useServerSearch` is gone with `api.search`: there is no `GET /search` to rank against.

export function useAssets(type?: string) {
  return useQuery({
    queryKey: ["assets", type ?? "all"],
    queryFn: () => api.assets(type),
  });
}

/**
 * Every semantic-layer item touching one physical column (GET
 * /columns/{column_id}/related; §14), resolved lazily when a column is opened in
 * the detail sheet. `columnId` null disables the query. Not retried on 404 so an
 * unresolvable column surfaces immediately rather than after backoff.
 */
export function useColumnRelated(columnId: string | null) {
  return useQuery({
    queryKey: ["column-related", columnId],
    enabled: columnId !== null,
    queryFn: () => api.columnRelated(columnId!),
    retry: false,
  });
}

/* ── conversations ─────────────────────────────────────────────────────────── */

/**
 * The server's persisted threads, newest first — the conversation switcher's list.
 *
 * Not one of the custom REST routes: threads are LangGraph Server's own resource, so this goes
 * through the SDK client (`lib/threads.ts`), which is also where the `select`/`extract`
 * projection that keeps the response at 4 KB instead of 2.4 MB lives.
 *
 * `refetchOnWindowFocus` is left on. A conversation started in another tab, or a turn that has
 * since finished, should be visible when you come back — this list's whole job is to be the
 * current answer to "what have I got open".
 */
export function useConversations(limit = 50) {
  return useQuery({
    queryKey: ["conversations", limit] as const,
    queryFn: () => listConversations(limit),
    enabled: !USE_MOCKS,
  });
}

/* ── the audit surface ─────────────────────────────────────────────────────── */
//
// Ungated: projections of the turn log and the loaded corpus, so no capability flag
// applies. `refetchOnWindowFocus` is left on for the turn list — it is a live tail of
// what the server has served, and a stale one invites the reader to conclude that a
// turn they just ran was never logged.

/** Served turns, newest first. */
export function useAuditTurns(limit = 50) {
  return useQuery({
    queryKey: ["audit-turns", limit] as const,
    queryFn: () => api.auditTurns(limit),
  });
}

/**
 * Clarifications the engine asked and nobody has answered, across every conversation.
 *
 * Not derived from `useAuditTurns`: an unanswered question never became a turn, so it is in no
 * audit row. Its only record is the platform's interrupt state, which is what
 * `GET /clarifications/pending` reads.
 *
 * `refetchInterval` because this is a queue rather than a log — a row appears when some other
 * reader stops mid-conversation, with no event on this client to notice it. Sixty seconds: the
 * thing being watched is a person deciding to leave a tab, which does not happen on a timescale
 * worth polling harder for.
 */
export function usePendingClarifications(limit = 50) {
  return useQuery({
    queryKey: ["pending-clarifications", limit] as const,
    queryFn: () => api.pendingClarifications(limit),
    refetchInterval: 60_000,
  });
}

/** One turn's full stage-by-stage trace. Disabled until a turn is selected. */
export function useAuditTrace(turnId: string | null) {
  return useQuery({
    queryKey: ["audit-trace", turnId] as const,
    queryFn: () => api.auditTrace(turnId as string),
    enabled: Boolean(turnId),
  });
}

/** Corpus shape plus its problems, split fatal vs degradation. */
export function useAuditCorpus() {
  return useQuery({
    queryKey: ["audit-corpus"] as const,
    queryFn: api.auditCorpus,
  });
}

/* ── filtering (ADR 0009) ──────────────────────────────────────────────────── */

/** The filterable columns of one asset type. Cached per type; the filter row is built
 * from this, so a field added to the engine appears with no change to this app. */
export function useCorpusFields(type: string) {
  return useQuery({
    queryKey: ["corpus-fields", type] as const,
    queryFn: () => api.corpusFields(type),
    enabled: Boolean(type),
    // Column *descriptions* change only when the engine's asset schema does, so a long
    // stale time here is not a staleness risk — it is one request per type per session.
    staleTime: 5 * 60 * 1000,
    // **Without this, switching asset type blanks the page.** A new type is a new query key,
    // so the query goes `pending`, `QueryState` swaps in the skeleton, and the entire table —
    // filter row included — unmounts and remounts. That is the "weird refresh" on navigating
    // the corpus page. Keeping the previous descriptor means `isPending` stays false and only
    // the contents change.
    placeholderData: keepPreviousData,
  });
}

/** One page of filtered rows. `keepPreviousData` so typing in a filter does not blank the
 * table between keystrokes — an empty table reads as "no matches", which is a wrong answer
 * while the next page is still in flight. */
export function useCorpusRows(params: {
  type: string;
  where?: CorpusWhere[];
  sort?: string;
  order?: "asc" | "desc";
  offset?: number;
  limit?: number;
}) {
  return useQuery({
    queryKey: [
      "corpus-rows",
      params.type,
      (params.where ?? [])
        .map((w) => `${w.field}:${w.op}:${w.value}`)
        .join("|"),
      params.sort ?? null,
      params.order ?? "asc",
      params.offset ?? 0,
      params.limit ?? 50,
    ] as const,
    queryFn: () => api.corpusRows(params),
    enabled: Boolean(params.type),
    placeholderData: keepPreviousData,
  });
}

/** The same rows, as one growing list: page N+1 is appended, never swapped in.
 *
 * This is what the corpus grid reads. `useCorpusRows` above stays for the *bounded* reads —
 * the schema and table dropdowns — which want exactly one page and no scroll behaviour.
 *
 * Two things make this safe on a 5,947-row type. The **server** still pages (`limit`/`offset`
 * unchanged, ADR 0009), so the transport is the same as before and nothing here asks for the
 * whole type at once. The **client** virtualizes, so the number of mounted rows is bounded by
 * the viewport rather than by how far you have scrolled. What grows without bound is the
 * flattened array of plain row objects in memory, which is what a corpus browser is for.
 */
export function useCorpusRowsInfinite(params: {
  type: string;
  where?: CorpusWhere[];
  sort?: string;
  order?: "asc" | "desc";
  limit?: number;
}) {
  const limit = params.limit ?? 100;
  return useInfiniteQuery({
    queryKey: [
      "corpus-rows-infinite",
      params.type,
      (params.where ?? [])
        .map((w) => `${w.field}:${w.op}:${w.value}`)
        .join("|"),
      params.sort ?? null,
      params.order ?? "asc",
      limit,
    ] as const,
    queryFn: ({ pageParam }) =>
      api.corpusRows({ ...params, offset: pageParam, limit }),
    initialPageParam: 0,
    // `offset + rows.length`, not `pages.length * limit`: the server is free to return short of
    // the limit, and deriving the next offset from the page count would then skip rows silently
    // — the failure mode you cannot see, because a browser of 5,947 assets missing 40 of them
    // looks exactly like one that is not.
    getNextPageParam: (last) => {
      const next = last.offset + last.rows.length;
      return last.rows.length > 0 && next < last.total ? next : undefined;
    },
    enabled: Boolean(params.type),
    // Typing in a filter must not blank the grid between keystrokes: an empty table reads as
    // "no matches", which is a wrong answer while the next request is still in flight.
    placeholderData: keepPreviousData,
  });
}

/* ── the return path (ADR 0015) ─────────────────────────────────────────────── */

/**
 * The review queue, grouped structurally.
 *
 * No `staleTime`: the queue is a worklist, and a steward who triages a row wants the next read to
 * reflect it. That is the opposite of `useCorpusFields`, whose five minutes are right because a
 * field descriptor changes when the engine is redeployed and not otherwise.
 */
export function useObservationClusters(limit = 200) {
  return useQuery({
    queryKey: ["observation-clusters", limit] as const,
    queryFn: () => api.observationClusters(limit),
    placeholderData: keepPreviousData,
  });
}

/** The flat queue, for the states a caller names. `undefined` means every state. */
export function useObservations(params: { state?: string; category?: string } = {}) {
  return useQuery({
    queryKey: ["observations", params.state ?? null, params.category ?? null] as const,
    queryFn: () => api.observations(params),
    placeholderData: keepPreviousData,
  });
}

/** One observation with its patches and history. Skipped while nothing is selected. */
export function useObservation(observationId: string | null) {
  return useQuery({
    queryKey: ["observation", observationId] as const,
    queryFn: () => api.observation(observationId as string),
    enabled: Boolean(observationId),
  });
}

/** Patches, newest first. `state` is comma-separated; `undefined` means every state. */
export function usePatches(state?: string) {
  return useQuery({
    queryKey: ["patches", state ?? null] as const,
    queryFn: () => api.patches({ state }),
    placeholderData: keepPreviousData,
  });
}

/**
 * Everything a return-path mutation has to invalidate.
 *
 * One list, because the three verbs all change the same two things — a row's state and whether a
 * patch hangs off it — and three hand-maintained lists is how a screen ends up showing a triaged
 * row as open until somebody reloads. `queryKey` prefixes match, so `["observation", id]` is
 * covered by `["observation"]`.
 */
const RETURN_PATH_KEYS = [
  ["observation-clusters"],
  ["observations"],
  ["observation"],
  ["patches"],
] as const;

function useInvalidateReturnPath(): () => Promise<void> {
  const client = useQueryClient();
  return async () => {
    await Promise.all(
      RETURN_PATH_KEYS.map((key) => client.invalidateQueries({ queryKey: key })),
    );
  };
}

/**
 * Move an observation.
 *
 * **No optimistic update, on purpose.** The server's transition table decides what is legal and
 * answers 409 when a move is not declared, so a client that painted the new state first would show
 * a state the store refused — and on a queue whose whole job is deciding, a wrong state on screen
 * is worse than a spinner. Same reason there is no rollback path to get wrong.
 */
export function useTriageObservation() {
  const invalidate = useInvalidateReturnPath();
  return useMutation({
    mutationFn: (vars: {
      observationId: string;
      to: string;
      detail?: string;
      decline_reason?: string;
      duplicate_of?: string;
      blocked_note?: string;
    }) => api.triageObservation(vars.observationId, vars),
    onSuccess: invalidate,
  });
}

/** Amend an untriaged observation's note. 409 once somebody has looked, which is not an error to
 *  retry — it is a sentence to show. */
export function useAmendObservation() {
  const invalidate = useInvalidateReturnPath();
  return useMutation({
    mutationFn: (vars: { observationId: string; note?: string; expected?: string }) =>
      api.amendObservation(vars.observationId, vars),
    onSuccess: invalidate,
  });
}

/**
 * Draft a patch. **This does not change the corpus and cannot.**
 *
 * It records what a change would be. Applying it is `git apply` and a commit in the corpus
 * repository, run by a person — which is why the surface's success message says a change was
 * drafted rather than made.
 */
export function useDraftPatch() {
  const invalidate = useInvalidateReturnPath();
  return useMutation({
    mutationFn: (body: Parameters<typeof api.draftPatch>[0]) => api.draftPatch(body),
    onSuccess: invalidate,
  });
}

/** Abandon a patch, with a reason the server requires. */
export function useWithdrawPatch() {
  const invalidate = useInvalidateReturnPath();
  return useMutation({
    mutationFn: (vars: { patchId: string; reason: string }) =>
      api.withdrawPatch(vars.patchId, vars.reason),
    onSuccess: invalidate,
  });
}
