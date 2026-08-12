/**
 * Client-side catalog: the normalized rows that back the Schema-tab search
 * omnibox and the namespace rail, plus a Fuse index over them.
 *
 * The catalog is deliberately source-agnostic (`CatalogItem.namespace`): it is
 * projected from either the full `/schema` dump or the lean `/schema/summary`
 * (both expose wire ``schema``). Keeping the projection here means the rest of
 * the UI never branches on which endpoint served the data.
 *
 * Q6 (D15): server `/search` stays deferred, so this client Fuse index is the
 * default — and permanent at expected corpus sizes.
 */

import Fuse from "fuse.js";

import type { CatalogItem, TableSummary, TableView } from "@/lib/types";

/** Project the full `/schema` dump into catalog rows (fallback / flat engine). */
export function toCatalog(tables: TableView[]): CatalogItem[] {
  return tables.map((t) => ({
    id: t.id,
    physical_name: t.physical_name,
    namespace: t.schema,
    row_count: t.row_count,
    n_columns: t.columns.length,
    excluded: t.excluded,
    has_suspect: t.columns.some((c) => c.reliability === "suspect"),
    provenance_status: t.provenance_status,
  }));
}

/** Project the lean `/schema/summary` items into catalog rows (scoped mode). */
export function summaryToCatalog(items: TableSummary[]): CatalogItem[] {
  return items.map((s) => ({
    id: s.id,
    physical_name: s.physical_name,
    namespace: s.schema,
    row_count: s.row_count,
    n_columns: s.n_columns,
    excluded: s.excluded,
    has_suspect: s.has_suspect,
    provenance_status: s.provenance_status,
  }));
}

/** Group catalog rows by namespace, with a per-namespace health rollup for the rail. */
export interface NamespaceRollup {
  namespace: string;
  tables: CatalogItem[];
  n_tables: number;
  n_suspect: number;
  n_excluded: number;
}

export function groupByNamespace(items: CatalogItem[]): NamespaceRollup[] {
  const byNs = new Map<string, CatalogItem[]>();
  for (const item of items) {
    const list = byNs.get(item.namespace);
    if (list) list.push(item);
    else byNs.set(item.namespace, [item]);
  }
  return Array.from(byNs.entries())
    .map(([namespace, tables]) => ({
      namespace,
      tables,
      n_tables: tables.length,
      n_suspect: tables.filter((t) => t.has_suspect).length,
      n_excluded: tables.filter((t) => t.excluded).length,
    }))
    .sort((a, b) => a.namespace.localeCompare(b.namespace));
}

const FUSE_OPTIONS: import("fuse.js").IFuseOptions<CatalogItem> = {
  keys: [
    { name: "physical_name", weight: 0.7 },
    { name: "namespace", weight: 0.3 },
  ],
  threshold: 0.4,
  ignoreLocation: true,
  minMatchCharLength: 2,
};

/** Build a reusable Fuse index (memoize on the item set to avoid per-keystroke rebuilds). */
export function buildCatalogIndex(items: CatalogItem[]): Fuse<CatalogItem> {
  return new Fuse(items, FUSE_OPTIONS);
}

/** Run a client-side fuzzy search; empty query returns everything (namespace-sorted). */
export function searchCatalog(index: Fuse<CatalogItem>, items: CatalogItem[], query: string): CatalogItem[] {
  const q = query.trim();
  if (q.length < 2) return items;
  return index.search(q).map((r) => r.item);
}
