/**
 * React Query hooks over the custom-route client. Consumers are Client
 * Components. Param-less keys stay stable so the whole app shares one cache;
 * scoped keys embed the scope so each D15 scope caches independently.
 *
 * **The scope hooks are no longer capability-gated.** They were, and the flat fallbacks they
 * gated are gone: `GET /schema` is deleted, and `can_scope === false` (which is also what an
 * unresolved `/capabilities` looks like) sent the graphs out unscoped and straight into the
 * client's alphabetical truncation — 150 nodes, 0 edges. `can_search` is still a real gate,
 * because the client Fuse index it falls back to genuinely works.
 */

"use client";

import { useMemo } from "react";
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api-client";
import { canSearch } from "@/lib/capabilities";
import { buildCatalogIndex, searchCatalog, summaryToCatalog } from "@/lib/catalog";
import { USE_MOCKS } from "@/lib/env";
import { listConversations } from "@/lib/threads";
import type { CatalogItem, CorpusWhere, SchemaScope, TableView } from "@/lib/types";

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
export function useSchemaSummary(scope?: SchemaScope, options?: { enabled?: boolean }) {
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
export function useCatalogSearch(items: CatalogItem[], query: string): CatalogItem[] {
  const index = useMemo(() => buildCatalogIndex(items), [items]);
  return useMemo(() => searchCatalog(index, items, query), [index, items, query]);
}

/** Server-ranked search (GET /search; gated on can_search, else no-op). */
export function useServerSearch(query: string) {
  const { data: caps } = useCapabilities();
  const enabled = canSearch(caps) && query.trim().length >= 2;
  return useQuery({
    queryKey: ["search", query],
    queryFn: () => api.search(query),
    enabled,
    placeholderData: keepPreviousData,
  });
}

export function useAssets(type?: string) {
  return useQuery({ queryKey: ["assets", type ?? "all"], queryFn: () => api.assets(type) });
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

/** Admin-answered clarifications folded into the corpus (GET /corpus/assumptions;
 * round 9), for the "agreed assumptions" log tab. */
export function useAssumptions() {
  return useQuery({ queryKey: ["assumptions"], queryFn: api.assumptions });
}

/** The approval queue (GET /corpus/drafts; fix round, task D), for the Drafts tab -- every
 * `proposed` asset, read fresh off disk on every call so an approval or a new draft is
 * visible within the same server process, unlike `useAssets`'s `/corpus/assets`. */
export function useDrafts() {
  return useQuery({ queryKey: ["drafts"], queryFn: api.drafts });
}

/** Round C: clarifications whose Enhancer decision CONTRADICTED an existing
 * asset (GET /corpus/conflicts), for the "Needs Review" tab. `status` filters
 * (e.g. "unresolved"); omit for every conflict, resolved or not. */
export function useConflicts(status?: string) {
  return useQuery({
    queryKey: ["conflicts", status ?? "all"],
    queryFn: () => api.conflicts(status),
  });
}

/** SME clarification ledger (GET /clarifications), for the admin clarification
 * panel. `status` filters on one exact value; omit for every record, which is
 * what that panel does — it needs two statuses (`open` and `deferred`) and the
 * route matches one. */
export function useClarifications(status?: string) {
  return useQuery({
    queryKey: ["clarifications", status ?? "all"],
    queryFn: () => api.clarifications(status),
  });
}

/** Reader-reported wrong answers (GET /feedback; utku-ai-trust-loop-plan.md, task H), for the
 * admin's Reports tab -- a second inbox, over a second ledger, beside `useClarifications` above
 * (H-b: never the same record type). `status` filters on one exact value; `FeedbackPanel`
 * always asks for `"open"`. */
export function useFeedback(status?: string) {
  return useQuery({
    queryKey: ["feedback", status ?? "all"],
    queryFn: () => api.feedback(status),
  });
}

/** Given a thread, what did it raise, and what became of it (GET /threads/{id}/raised;
 * utku-ai-trust-loop-plan.md, task B-1), for `raised-history.tsx` (task B-2). `options.enabled`
 * mirrors `useSchemaSummary`'s own shape: the caller additionally gates on tier
 * (`tierShowsRaisedHistory`), and a query this route's own render never uses should not run
 * either -- the same reasoning `answer-card.tsx`'s `needsCatalogGlimpse` gate already applies to
 * its own conditional fetch. Always disabled with no thread id, regardless of `options.enabled`:
 * a fresh conversation has raised nothing yet. */
export function useRaisedByThread(threadId: string | null, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["raised", threadId ?? "none"],
    queryFn: () => api.raisedByThread(threadId!),
    enabled: threadId !== null && (options?.enabled ?? true),
  });
}

/** Phase 1 elicitation wizard candidates (GET /elicitation/candidates) — every
 * `source="elicitation_wizard"` ledger record, open and answered, for the
 * proactive admin onboarding flow's grouped A > C+E > B > D view. */
export function useElicitationCandidates() {
  return useQuery({
    queryKey: ["elicitation-candidates"],
    queryFn: api.elicitationCandidates,
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
  return useQuery({ queryKey: ["audit-corpus"] as const, queryFn: api.auditCorpus });
}

/** Does the loop turn, and where does it stop (utku-ai-trust-loop-plan.md, task C) -- the
 * refusals → reader entrances → approved rules → retrieved-again funnel, for the admin/engineer
 * metrics view (`components/corpus/trust-loop-metrics.tsx`). Ungated like the audit queries
 * above: a projection of the same turn log and corpus, gated on tier alone at the render site
 * (`tierShowsTrustLoopMetrics`), never on a capability flag this route does not itself require. */
export function useTrustLoopMetrics() {
  return useQuery({ queryKey: ["trust-loop-metrics"] as const, queryFn: api.trustLoopMetrics });
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
      (params.where ?? []).map((w) => `${w.field}:${w.op}:${w.value}`).join("|"),
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
      (params.where ?? []).map((w) => `${w.field}:${w.op}:${w.value}`).join("|"),
      params.sort ?? null,
      params.order ?? "asc",
      limit,
    ] as const,
    queryFn: ({ pageParam }) => api.corpusRows({ ...params, offset: pageParam, limit }),
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
