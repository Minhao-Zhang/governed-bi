"use client";

/**
 * Schema-tab search omnibox. A single text input over the full catalog: the
 * default engine is the synchronous client Fuse index (`useCatalogSearch`), and
 * when the backend reports `can_search` the server-ranked `/search` reorders the
 * same catalog rows (falling back to the client order when the server query is
 * disabled or returns nothing). Results are grouped by namespace; picking one
 * lifts the selection and narrows the scope onto that table.
 *
 * Cross-schema namespaces are shown as ordinary, subtle badges — never warnings
 * (D15 Q7). The only "loud" channel stays the reliability one (suspect/excluded).
 */

import { useMemo, useState } from "react";
import { Search, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { groupByNamespace } from "@/lib/catalog";
import { canSearch } from "@/lib/capabilities";
import {
  useCapabilities,
  useCatalog,
  useCatalogSearch,
  useServerSearch,
} from "@/hooks/queries";
import type { CatalogItem, GraphSelection, SchemaScope } from "@/lib/types";

export function SchemaSearch({
  scope,
  onScopeChange,
  onSelect,
}: {
  scope: SchemaScope;
  onScopeChange: (next: SchemaScope) => void;
  onSelect: (selection: GraphSelection) => void;
}) {
  const { data: caps } = useCapabilities();
  const serverEnabled = canSearch(caps);

  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  // Search the whole corpus; picking a hit still narrows the rail scope.
  const { items } = useCatalog();
  const clientResults = useCatalogSearch(items, query);
  const server = useServerSearch(query); // no-op unless can_search

  // Server (when available + non-empty) reorders the same catalog rows; otherwise
  // the client Fuse order stands. Grouping always runs on catalog rows so the row
  // render is identical regardless of which engine ranked them.
  const results = useMemo<CatalogItem[]>(() => {
    if (serverEnabled && server.data && server.data.hits.length > 0) {
      const byId = new Map(items.map((it) => [it.id, it]));
      const seen = new Set<string>();
      const ordered: CatalogItem[] = [];
      for (const hit of server.data.hits) {
        const key = hit.table_id ?? hit.id;
        const it = byId.get(key);
        if (it && !seen.has(it.id)) {
          seen.add(it.id);
          ordered.push(it);
        }
      }
      if (ordered.length > 0) return ordered;
    }
    return clientResults;
  }, [serverEnabled, server.data, items, clientResults]);

  const groups = useMemo(() => groupByNamespace(results), [results]);

  // Flat order matches the rendered (namespace-grouped) order for keyboard nav.
  const flat = useMemo(() => groups.flatMap((g) => g.tables), [groups]);

  // Clamp during render so the highlight stays in range as results shrink,
  // without a setState-in-effect cascade.
  const activeIndex = flat.length === 0 ? 0 : Math.min(active, flat.length - 1);

  const showPanel = open && query.trim().length > 0;

  const pick = (item: CatalogItem) => {
    onSelect({ id: item.id, kind: "table", label: item.physical_name });
    onScopeChange({ ...scope, schema: item.namespace, focus: item.id });
    setOpen(false);
  };

  const clear = () => {
    setQuery("");
    setActive(0);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((a) => Math.min(a + 1, Math.max(flat.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      const item = flat[activeIndex];
      if (item) {
        e.preventDefault();
        pick(item);
      }
    } else if (e.key === "Escape") {
      if (query) clear();
      else setOpen(false);
    }
  };

  return (
    <div className="relative">
      <div className="relative">
        <Search
          className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
          placeholder="Search tables…"
          role="combobox"
          aria-expanded={showPanel}
          aria-controls="schema-search-results"
          className="pl-8 pr-8"
        />
        {query && (
          <button
            type="button"
            onClick={clear}
            aria-label="Clear search"
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <X className="size-3.5" aria-hidden />
          </button>
        )}
      </div>

      {showPanel && (
        <div
          id="schema-search-results"
          role="listbox"
          className="absolute z-20 mt-1 max-h-[50vh] w-full overflow-auto rounded-lg border bg-popover p-1 text-popover-foreground shadow-md"
          // Keep focus on the input so blur doesn't race the click.
          onMouseDown={(e) => e.preventDefault()}
        >
          {flat.length === 0 ? (
            <p className="px-2 py-3 text-center text-xs text-muted-foreground">
              No tables match “{query.trim()}”.
            </p>
          ) : (
            groups.map((group) => (
              <div key={group.namespace} className="mb-1 last:mb-0">
                <div className="flex items-center justify-between px-2 py-1">
                  <span className="text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground">
                    {group.namespace}
                  </span>
                  <span className="text-[0.7rem] text-muted-foreground">
                    {group.n_tables}
                  </span>
                </div>
                {group.tables.map((item) => {
                  const idx = flat.indexOf(item);
                  const isActive = idx === activeIndex;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      role="option"
                      aria-selected={isActive}
                      onClick={() => pick(item)}
                      onMouseEnter={() => setActive(idx)}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors",
                        isActive ? "bg-muted" : "hover:bg-muted/60",
                      )}
                    >
                      <span className="min-w-0 flex-1 truncate font-mono text-sm">
                        {item.physical_name}
                      </span>
                      <Badge variant="outline" className="text-muted-foreground">
                        {item.namespace}
                      </Badge>
                      {item.has_suspect && (
                        <Badge
                          variant="outline"
                          className="border-tier-lineage/50 text-tier-lineage"
                        >
                          suspect
                        </Badge>
                      )}
                      {item.excluded && (
                        <Badge
                          variant="outline"
                          className="border-tier-refused/40 text-tier-refused"
                        >
                          excluded
                        </Badge>
                      )}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
