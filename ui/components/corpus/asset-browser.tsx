"use client";

/**
 * Search across every asset type at once: one ranked, virtualized list.
 *
 * Tables are included alongside metrics/terms/joins/few-shots/negatives, so this
 * reports the whole corpus — `/corpus/assets` serves only the non-table assets,
 * which left it 705 tables short of the count `/audit/corpus` gives.
 *
 * Search is a weighted Fuse index (`lib/asset-catalog.ts`) rather than substring
 * matching, over three structured filters — schema, provenance status, excluded —
 * which are the axes a reviewer actually triages on. The list is virtualized: at
 * ~4.2k rows, rendering every match was the real ceiling here.
 *
 * **This surface locates; it does not explain.** Every type is here, so the only
 * columns it can show are the six they share — which is exactly why picking a hit
 * hands off to the by-type table (`onLocate`), scoped to that asset, where every
 * field of its own type is a click away. Before, the two tabs were two lists that
 * had never heard of each other: you found the thing here and looked it up again
 * by hand there.
 *
 * **There is no edit affordance, and there cannot be one.** A pencil button used to appear
 * when `capabilities.can_edit`, opening a sheet that POSTed to `/corpus/edit`. Neither the
 * route nor the flag exists: `api/routes.py::capabilities_for` hardcodes `can_edit: false`
 * with `edit_mode: "none"` because the curator is out of scope of the served surface
 * (ADR 0007 §7), so the button was reachable only under the mock transport, where it posted
 * nowhere and reported "Editing requires a connected dev backend." Writing the corpus is a
 * git/PR job against the corpus repository. Deleted with `<AssetEditSheet/>`.
 */

import { useDeferredValue, useMemo, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";

import { cn } from "@/lib/utils";
import { CORPUS_ASSET_TYPES } from "@/lib/types";
import { useAssets, useCatalog } from "@/hooks/queries";
import {
  EMPTY_FILTERS,
  applyStructured,
  applyType,
  createAssetSearcher,
  provenanceOptions,
  mergeAssetCatalog,
  tallyByType,
  type AssetCatalogItem,
  type AssetFilters,
} from "@/lib/asset-catalog";
import { QueryState } from "@/components/common/query-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** Shared grid so the header and every virtualized row line up. */
const GRID = "grid grid-cols-[minmax(0,18rem)_7rem_minmax(0,1fr)_6.5rem_5rem_auto] items-center gap-3";

// A type is named `few_shot` here, in the type pills, and on the by-type tab's pills — the
// engine's token, in mono, everywhere. This surface used to prettify it to "few shot" and
// capitalize it, so the same asset type was two words on one tab and one on the other. The
// wire vocabulary is the vocabulary (`api/browse.py` parses these), and a reader who has to
// map "Few shot" onto `few_shot` is doing translation the UI invented.

/**
 * Outline-badge className for a provenance status, matching the app's trust
 * semantics: certified → governed (green), heuristic → lineage (amber),
 * anything else (incl. null) → muted.
 */
function provenanceClass(status: string | null): string {
  if (status === "certified") return "border-tier-governed/40 text-tier-governed";
  if (status === "heuristic") return "border-tier-lineage/50 text-tier-lineage";
  return "text-muted-foreground";
}

export function AssetBrowser({
  onLocate,
}: {
  /** Hands a hit to the by-type table, which is the only surface that can show its own type's
   * fields. Optional so this list still renders (inert rows) if it is ever mounted alone. */
  onLocate?: (row: { id: string; asset_type: string }) => void;
}) {
  const assets = useAssets();
  const { items: tables, isLoading: tablesLoading } = useCatalog();
  // Query is separate state from the structured filters ON PURPOSE — see the note
  // on AssetFilters. Bundling them made every keystroke invalidate the memos twice.
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<AssetFilters>(EMPTY_FILTERS);

  const set = <K extends keyof AssetFilters>(key: K, value: AssetFilters[K]) =>
    setFilters((prev) => ({ ...prev, [key]: value }));

  // One catalog over both sources; rebuilt only when a source changes.
  const catalog = useMemo(
    () => mergeAssetCatalog(assets.data ?? [], tables),
    [assets.data, tables],
  );
  // Building the index over ~4.2k rows is the expensive step — do it once per
  // catalog. The searcher also memoizes per query, so backspacing is free.
  const searcher = useMemo(() => createAssetSearcher(catalog), [catalog]);

  // The pipeline is staged so each keystroke does exactly ONE Fuse pass, and the
  // input itself never waits on it:
  //   deferred query -> searcher.search (expensive, ~30-60ms; cached per query)
  //                  -> applyStructured (cheap array pass)
  //                  -> tallyByType + applyType (cheap)
  // `useDeferredValue` lets the keystroke paint at high priority and re-runs the
  // pipeline at low priority; because the memo below depends only on the deferred
  // value, it does not also fire on the immediate render.
  const deferredQuery = useDeferredValue(query);
  const searched = useMemo(
    () => searcher.search(deferredQuery),
    [searcher, deferredQuery],
  );
  // Everything except the type filter — the basis for both the counts and the rows,
  // so the two can never disagree and the search is not repeated for each.
  const scoped = useMemo(() => applyStructured(searched, filters), [searched, filters]);
  const { counts, total } = useMemo(() => tallyByType(scoped, CORPUS_ASSET_TYPES), [scoped]);
  const rows = useMemo(() => applyType(scoped, filters.type), [scoped, filters.type]);
  // True while the deferred pipeline is still catching up with the input.
  const searching = deferredQuery !== query;

  const schemas = useMemo(
    () => Array.from(new Set(tables.map((t) => t.namespace))).sort((a, b) => a.localeCompare(b)),
    [tables],
  );
  const provenances = useMemo(() => provenanceOptions(catalog), [catalog]);
  const loaded = assets.data !== undefined && !tablesLoading;
  const filtered =
    filters.type !== "all" ||
    filters.namespace !== "all" ||
    filters.provenance !== "all" ||
    filters.excludedOnly ||
    query.trim() !== "";

  return (
    // Same deal as the by-type table: the search box, the filters and the type pills stay put,
    // and the hit list below is the only thing that scrolls.
    <div className="flex h-full min-h-0 flex-col gap-4">
      {/* Text search + structured filters. */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[16rem] flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search all corpus assets…"
            aria-label="Search corpus assets"
            className="pl-8"
          />
        </div>

        <FilterSelect
          label="Schema"
          value={filters.namespace}
          onChange={(v) => set("namespace", v)}
          options={[
            { value: "all", label: `All schemas (${schemas.length})` },
            ...schemas.map((s) => ({ value: s, label: s })),
            // Only offered when something actually failed to resolve, so the
            // option never implies a gap that isn't there.
            ...(catalog.some((i) => i.namespace === null)
              ? [{ value: "unknown", label: "— unresolved" }]
              : []),
          ]}
        />

        <FilterSelect
          label="Provenance"
          value={filters.provenance}
          onChange={(v) => set("provenance", v)}
          options={[
            { value: "all", label: "Any provenance" },
            ...provenances.map((p) => ({ value: p, label: p })),
          ]}
        />

        <Button
          type="button"
          size="sm"
          variant={filters.excludedOnly ? "secondary" : "ghost"}
          aria-pressed={filters.excludedOnly}
          onClick={() => set("excludedOnly", !filters.excludedOnly)}
          className={cn(!filters.excludedOnly && "text-muted-foreground")}
        >
          Excluded only
        </Button>

        {filtered && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              setFilters(EMPTY_FILTERS);
              setQuery("");
            }}
            className="text-muted-foreground"
          >
            <X className="size-3.5" />
            Clear
          </Button>
        )}
      </div>

      {/* Type pills, each carrying how many the other filters leave it. */}
      <div className="flex flex-wrap items-center gap-1.5">
        <FilterToggle
          active={filters.type === "all"}
          onClick={() => set("type", "all")}
          count={loaded ? total : undefined}
        >
          All
        </FilterToggle>
        {CORPUS_ASSET_TYPES.map((type) => (
          <FilterToggle
            key={type}
            active={filters.type === type}
            onClick={() => set("type", type)}
            count={loaded ? counts[type] : undefined}
          >
            <span className="font-mono">{type}</span>
          </FilterToggle>
        ))}
      </div>

      <QueryState
        query={assets}
        isEmpty={(data) => data.length === 0 && tables.length === 0}
        emptyMessage="No corpus assets."
      >
        {() =>
          rows.length === 0 ? (
            <p className="rounded-lg border border-dashed py-10 text-center text-sm text-muted-foreground">
              {query.trim()
                ? `No assets match “${query.trim()}”.`
                : "No assets match these filters."}
            </p>
          ) : (
            // Dim while the deferred pipeline catches up, so a stale list is
            // visibly stale instead of looking like the answer to what you typed.
            <div
              className={cn(
                "flex min-h-0 flex-1 flex-col transition-opacity",
                searching && "opacity-60",
              )}
            >
              <AssetList rows={rows} onLocate={onLocate} />
            </div>
          )
        }
      </QueryState>
    </div>
  );
}

/** Virtualized asset rows under a sticky column header. */
function AssetList({
  rows,
  onLocate,
}: {
  rows: AssetCatalogItem[];
  onLocate?: (row: { id: string; asset_type: string }) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 44,
    overscan: 12,
  });

  return (
    <div className="flex min-h-0 flex-1 flex-col rounded-md border">
      <div
        className={cn(
          GRID,
          "shrink-0 border-b bg-muted/30 px-3 py-2 text-xs font-medium text-muted-foreground",
        )}
      >
        <span>ID</span>
        <span>Type</span>
        <span>Summary</span>
        <span>Schema</span>
        <span>Provenance</span>
        {/* The sixth column carries the exclusion / suspect badges, which need no header. */}
        <span aria-hidden />
      </div>

      {/* The virtualizer's scroll element, and now it is sized by the page rather than by a
          `max-h-[65vh]` guess — 65vh left a list that was short of the viewport *and* a page
          that scrolled around it, so a drag could move either one. */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto">
        <div className="relative w-full" style={{ height: `${rowVirtualizer.getTotalSize()}px` }}>
          {rowVirtualizer.getVirtualItems().map((virtualRow) => {
            const row = rows[virtualRow.index];
            return (
              <div
                key={row.id}
                data-index={virtualRow.index}
                ref={rowVirtualizer.measureElement}
                className="absolute left-0 top-0 w-full"
                style={{ transform: `translateY(${virtualRow.start}px)` }}
              >
                <AssetListRow row={row} onLocate={onLocate} />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function AssetListRow({
  row,
  onLocate,
}: {
  row: AssetCatalogItem;
  onLocate?: (row: { id: string; asset_type: string }) => void;
}) {
  const locate = onLocate ? () => onLocate({ id: row.id, asset_type: row.asset_type }) : undefined;
  return (
    <div
      className={cn(
        GRID,
        "border-b px-3 py-2 text-sm last:border-b-0 hover:bg-muted/40",
        locate && "cursor-pointer",
      )}
      role={locate ? "button" : undefined}
      tabIndex={locate ? 0 : undefined}
      title={locate ? "Open in the by-type view" : undefined}
      onClick={locate}
      onKeyDown={(e) => {
        if (locate && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          locate();
        }
      }}
    >
      <span className="truncate font-mono text-xs" title={row.id}>
        {row.name || row.id}
      </span>
      <span>
        <Badge variant="outline" className="font-mono">
          {row.asset_type}
        </Badge>
      </span>
      <span className="truncate text-muted-foreground" title={row.summary}>
        {row.summary}
      </span>
      <span className="truncate font-mono text-xs text-muted-foreground" title={row.namespace ?? ""}>
        {row.namespace ?? "—"}
      </span>
      <span className="flex items-center gap-1">
        {row.provenance_status ? (
          <Badge variant="outline" className={provenanceClass(row.provenance_status)}>
            {row.provenance_status}
          </Badge>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </span>
      <span className="flex items-center justify-end gap-1">
        {row.excluded && (
          <Badge variant="outline" className="border-tier-refused/40 text-tier-refused">
            excluded
          </Badge>
        )}
        {row.has_suspect && (
          <Badge variant="outline" className="border-tier-lineage/50 text-tier-lineage">
            suspect
          </Badge>
        )}
      </span>
    </div>
  );
}

/** A labelled native select. Native on purpose: with 67 schemas the platform's own
 * scrolling and type-ahead beat anything a custom popover would give here. */
function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        "h-9 rounded-md border border-input bg-transparent px-2 text-sm outline-none transition-colors",
        "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
        value === "all" ? "text-muted-foreground" : "text-foreground",
      )}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

/**
 * A pill-style toggle for the type row, with an optional count badge.
 *
 * `count` is undefined while the fetch is in flight (no badge) and 0 for a type
 * the corpus genuinely has none of — the badge then goes muted so the eye skips
 * it. The number is folded into the button's accessible name so a screen reader
 * hears "metric, 1312 assets" rather than the digits floating free of the label.
 */
function FilterToggle({
  active,
  onClick,
  count,
  children,
}: {
  active: boolean;
  onClick: () => void;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant={active ? "secondary" : "ghost"}
      aria-pressed={active}
      onClick={onClick}
      className={cn(!active && "text-muted-foreground")}
    >
      {children}
      {count !== undefined && (
        <>
          <span
            aria-hidden
            className={cn(
              "ml-1 rounded-sm px-1 py-px text-[11px] tabular-nums",
              count === 0
                ? "text-muted-foreground/60"
                : active
                  ? "bg-background/70 text-foreground"
                  : "bg-muted text-muted-foreground",
            )}
          >
            {count.toLocaleString()}
          </span>
          <span className="sr-only">, {count.toLocaleString()} assets</span>
        </>
      )}
    </Button>
  );
}
