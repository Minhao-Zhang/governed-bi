/**
 * The unified corpus catalog: every asset kind in one searchable list.
 *
 * `/corpus/assets` deliberately serves only the NON-table assets, so the Corpus
 * page used to under-report the corpus — 3,464 rows against the 4,169 assets
 * `/health` counts, the difference being 705 tables. But a table *is* a corpus
 * asset in the domain model (curator-written description and grain,
 * `provenance_status`, `confidence`, `excluded`), so this module folds the table
 * catalog back in and gives the whole set one Fuse index and one filter model.
 *
 * Tables keep their own scoped browser on the Schema tab; that view answers "what
 * is in *this* namespace, with columns, beside the diagram". This one answers
 * "find any asset anywhere", which is a different question.
 */

import Fuse from "fuse.js";

import type { AssetRow, CatalogItem } from "@/lib/types";

/** One row of the unified catalog, projected from either source. */
export interface AssetCatalogItem {
  id: string;
  asset_type: string;
  /** Display/search name. The physical name for tables; empty for asset kinds
   * whose name already lives in `summary`. */
  name: string;
  summary: string;
  provenance_status: string | null;
  excluded: boolean;
  /** Owning schema, or null when it cannot be resolved — see `deriveNamespace`. */
  namespace: string | null;
  /** Tables only: at least one column is curator-flagged suspect. */
  has_suspect: boolean;
}

/** Asset-id type prefixes, longest first so `neg_`/`note_` can't shadow each other. */
const TYPE_PREFIXES = ["metric_", "note_", "term_", "join_", "neg_", "tbl_", "fs_"];

/**
 * Recover an asset's owning schema from its id.
 *
 * `AssetRowResponse` carries no namespace, so this is a client-side derivation —
 * but not a guess: it strips the type prefix and then matches the remainder
 * against the **authoritative** schema list from the table catalog, longest name
 * first so `sales` can never shadow `sales_in_weather`. Measured against the live
 * 67-schema corpus it resolves 3,464/3,464 assets.
 *
 * Returns null when nothing matches (a genuinely global asset, or a corpus whose
 * ids don't embed the schema). Callers must treat null as "unknown", never as a
 * schema — a filter that silently drops unresolved rows would hide assets.
 *
 * The real fix is `schema` on the wire; this exists so the filter works today.
 */
export function deriveNamespace(id: string, schemasLongestFirst: string[]): string | null {
  let rest = id;
  for (const prefix of TYPE_PREFIXES) {
    if (rest.startsWith(prefix)) {
      rest = rest.slice(prefix.length);
      break;
    }
  }
  for (const schema of schemasLongestFirst) {
    if (rest === schema || rest.startsWith(`${schema}_`)) return schema;
  }
  return null;
}

/** Schema names, longest first — the order `deriveNamespace` needs. */
export function schemasByLengthDesc(tables: CatalogItem[]): string[] {
  return Array.from(new Set(tables.map((t) => t.namespace))).sort(
    (a, b) => b.length - a.length || a.localeCompare(b),
  );
}

/** Human summary for a table row. The lean `/schema/summary` drops descriptions,
 * so this states the shape rather than inventing prose. */
function tableSummary(t: CatalogItem): string {
  const parts = [`${t.n_columns} column${t.n_columns === 1 ? "" : "s"}`];
  if (t.row_count != null) parts.push(`${t.row_count.toLocaleString()} rows`);
  return parts.join(" · ");
}

/**
 * Merge the non-table assets with the table catalog into one list, sorted by
 * type then id so the order is stable across renders and backends.
 */
export function mergeAssetCatalog(
  assets: AssetRow[],
  tables: CatalogItem[],
): AssetCatalogItem[] {
  const schemas = schemasByLengthDesc(tables);
  const fromAssets: AssetCatalogItem[] = assets.map((a) => ({
    id: a.id,
    asset_type: a.asset_type,
    name: "",
    summary: a.summary,
    provenance_status: a.provenance_status,
    excluded: a.excluded,
    // The engine's own namespace when it sends one; the id-prefix derivation only as a
    // fallback. `/corpus/assets` carries `schema`, and guessing a namespace by matching id
    // prefixes gets it wrong for any schema whose name prefixes another.
    namespace: a.schema ?? deriveNamespace(a.id, schemas),
    has_suspect: false,
  }));
  const fromTables: AssetCatalogItem[] = tables.map((t) => ({
    id: t.id,
    asset_type: "table",
    name: t.physical_name,
    summary: tableSummary(t),
    provenance_status: t.provenance_status,
    excluded: t.excluded,
    // Tables carry their namespace on the wire — no derivation needed.
    namespace: t.namespace,
    has_suspect: t.has_suspect,
  }));
  // **Deduplicated by id, tables winning.** Both sources contain every table:
  // `/corpus/assets` returns all 13 981 assets (tables among them) and the lean catalog
  // returns the 656 tables, under the *same* asset ids. Concatenating them therefore emitted
  // each table twice — which React reported as "two children with the same key,
  // `address.alias`", and which silently doubled the type tallies beside the filters.
  //
  // It was invisible until recently only because `/corpus/assets` was failing its zod
  // boundary, so `assets.data` was `undefined` and this merge received an empty list. Fixing
  // that route revealed the collision rather than causing it, which is the useful shape of
  // this bug: a broken route was standing in for a missing dedupe.
  //
  // Tables win because their row is strictly richer — a physical name, curated prose, the
  // namespace on the wire and `has_suspect`, none of which the plain asset row carries.
  const byId = new Map<string, AssetCatalogItem>();
  for (const item of fromAssets) byId.set(item.id, item);
  for (const item of fromTables) byId.set(item.id, item);
  return [...byId.values()].sort(
    (a, b) => a.asset_type.localeCompare(b.asset_type) || a.id.localeCompare(b.id),
  );
}

/* ── Search ──────────────────────────────────────────────────────────────── */

/** Weighted so a table's physical name outranks an incidental id substring, and
 * the curator's prose (`summary`) outranks the mechanical id. Mirrors the
 * thresholds `lib/catalog.ts` already uses for the schema omnibox. */
const FUSE_OPTIONS: import("fuse.js").IFuseOptions<AssetCatalogItem> = {
  keys: [
    { name: "name", weight: 0.5 },
    { name: "summary", weight: 0.3 },
    { name: "id", weight: 0.2 },
  ],
  threshold: 0.4,
  ignoreLocation: true,
  minMatchCharLength: 2,
};

export function buildAssetFuse(items: AssetCatalogItem[]): Fuse<AssetCatalogItem> {
  return new Fuse(items, FUSE_OPTIONS);
}

/** Bounded so a long session can't grow it without limit; far more than the
 * handful of prefixes one search interaction actually revisits. */
const SEARCH_CACHE_MAX = 64;

/**
 * A searcher that owns the Fuse index and memoizes results per query string.
 *
 * `useMemo` only caches the *latest* result, which loses the most common editing
 * move: backspacing. Correcting "customerz" back to "customer" re-pays a full
 * 30-60 ms scan for a query already computed a moment earlier. Every prefix walked
 * on the way in is a cache hit on the way out.
 */
export interface AssetSearcher {
  search(query: string): AssetCatalogItem[];
}

export function createAssetSearcher(items: AssetCatalogItem[]): AssetSearcher {
  const fuse = new Fuse(items, FUSE_OPTIONS);
  const cache = new Map<string, AssetCatalogItem[]>();
  return {
    search(query: string): AssetCatalogItem[] {
      const q = query.trim();
      // Identity-stable for the no-op case so downstream memos don't invalidate.
      if (q.length < MIN_QUERY_LENGTH) return items;
      const hit = cache.get(q);
      if (hit) return hit;
      const result = fuse.search(q).map((r) => r.item);
      if (cache.size >= SEARCH_CACHE_MAX) {
        // Evict oldest (Map preserves insertion order) — plain FIFO is enough
        // here; a true LRU would need a touch on every hit for no real gain.
        const oldest = cache.keys().next().value;
        if (oldest !== undefined) cache.delete(oldest);
      }
      cache.set(q, result);
      return result;
    },
  };
}

/**
 * The structured (non-text) filters. Deliberately does NOT include the query.
 *
 * Keeping the text query in separate state is a performance requirement, not
 * tidiness: a combined object changes identity on every keystroke, so any memo
 * depending on it recomputes immediately with the *stale* deferred query and then
 * again when the deferred value lands — which silently doubles the work and
 * defeats the deferral it was supposed to cooperate with.
 */
export interface AssetFilters {
  /** Asset type, or "all". */
  type: string;
  /** Schema name, "all", or "unknown" for rows with no resolvable namespace. */
  namespace: string;
  /** `provenance_status`, or "all". */
  provenance: string;
  /** Show only governance-excluded assets. */
  excludedOnly: boolean;
}

export const EMPTY_FILTERS: AssetFilters = {
  type: "all",
  namespace: "all",
  provenance: "all",
  excludedOnly: false,
};

/** Fuse's own `minMatchCharLength` is 2, so a single character cannot match a
 * token anyway — searching on it costs a full scan for a meaningless result. */
const MIN_QUERY_LENGTH = 2;

/**
 * The one expensive pass: fuzzy-rank the catalog against `query`.
 *
 * Call this **once** per settled query and derive everything else from the result.
 * Fuse over ~4.2k rows with three weighted keys measures 15–60 ms, so each extra
 * call is a dropped frame. Below `MIN_QUERY_LENGTH` it returns the catalog
 * untouched (identity-stable, so downstream memos don't invalidate).
 */
export function searchCatalog(
  items: AssetCatalogItem[],
  fuse: Fuse<AssetCatalogItem>,
  query: string,
): AssetCatalogItem[] {
  const q = query.trim();
  if (q.length < MIN_QUERY_LENGTH) return items;
  return fuse.search(q).map((r) => r.item);
}

/** The structured predicates, minus `type` — so the type-pill counts can apply
 * everything *except* the type the pills are offering. */
export function matchesStructured(
  item: AssetCatalogItem,
  filters: Omit<AssetFilters, "type">,
): boolean {
  if (filters.excludedOnly && !item.excluded) return false;
  if (filters.provenance !== "all" && item.provenance_status !== filters.provenance) return false;
  if (filters.namespace === "unknown") return item.namespace === null;
  if (filters.namespace !== "all" && item.namespace !== filters.namespace) return false;
  return true;
}

/** Apply the structured filters to an already-searched list. A plain array pass —
 * no Fuse, so this is cheap enough to re-run whenever a filter toggles. */
export function applyStructured(
  items: AssetCatalogItem[],
  filters: Omit<AssetFilters, "type">,
): AssetCatalogItem[] {
  return items.filter((item) => matchesStructured(item, filters));
}

/** Narrow to one asset type (or pass everything through for "all"). */
export function applyType(items: AssetCatalogItem[], type: string): AssetCatalogItem[] {
  return type === "all" ? items : items.filter((item) => item.asset_type === type);
}

/**
 * Tally per-type counts over rows the *other* filters already allowed. Pure
 * counting — it must never re-run the search, which is exactly the mistake that
 * made every keystroke pay for two identical Fuse passes.
 */
export function tallyByType(
  items: AssetCatalogItem[],
  types: readonly string[],
): { counts: Record<string, number>; total: number } {
  const counts: Record<string, number> = Object.fromEntries(types.map((t) => [t, 0]));
  for (const item of items) counts[item.asset_type] = (counts[item.asset_type] ?? 0) + 1;
  return { counts, total: items.length };
}

/** Distinct provenance statuses present, for the filter's options. */
export function provenanceOptions(items: AssetCatalogItem[]): string[] {
  return Array.from(
    new Set(items.map((i) => i.provenance_status).filter((s): s is string => s !== null)),
  ).sort();
}
