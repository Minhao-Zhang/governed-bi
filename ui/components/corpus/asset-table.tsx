"use client";

/**
 * The corpus data table: **every column filterable, and the columns are not written here.**
 *
 * ADR 0009 D1. `GET /corpus/fields?type=…` returns one descriptor per field of the asset
 * dataclass — its `kind`, the operators it accepts, whether it sorts — and this component
 * renders a control per descriptor. So a field added to the engine's `corpus/schema.py`
 * becomes filterable with no change to this file. A hand-written column list here would
 * drift, and it would drift silently: a missing column is indistinguishable from a column
 * somebody chose not to expose.
 *
 * Filtering, sorting and paging all run **server-side** over the loaded corpus. The alternative
 * was what this page did before: fetch all 13,981 assets (2.25 MB measured) and filter in the
 * browser, with three fixed filter axes instead of every column.
 *
 * **The paging is no longer a control.** It was `1–50 of 656` and a prev/next pair — a reader
 * asking the server for row 200 by pressing a button four times. Now the grid scrolls and the
 * next page is fetched as the last rows come into view. Two things keep that honest and cheap:
 *
 *   - the rows are **virtualized**, so what is mounted is bounded by the viewport rather than by
 *     how far anyone has scrolled (a `column` type is 5,947 rows);
 *   - the footer still prints `of {total}`, because a scrollbar reports the *loaded* list and a
 *     reader who has pulled 200 of 5,947 rows would otherwise read a nearly-full scrollbar as
 *     the whole corpus.
 *
 * Two honesty properties this component must keep, because the server went to the trouble of
 * reporting them:
 *
 *   - `unknown_where` — predicates the server could not apply. Rendered as a warning. A
 *     dropped filter shows a filtered-looking list that is not filtered.
 *   - `total` is the count **after** filtering, so the footer count moves when a filter does.
 *     Showing an unfiltered total beside a filtered page is how a reader concludes their
 *     filter did nothing. It is also why the per-type corpus counts are *not* on the type
 *     pills here and live in the header's status popover instead: `column 5,947` above 87
 *     scoped rows is that same wrong answer, printed in a place a reader trusts more.
 *
 * A row opens its own record (`AssetRecordSheet`) — the fields this grid clamps to three lines
 * and the columns it hides by default. Rows used to open the graph's node sheet, which for
 * anything but a table showed `id / kind / label`: three fields the row already had.
 */

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, Columns3, ListFilter, X } from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";

import { cn } from "@/lib/utils";
import { useCorpusFields, useCorpusRows, useCorpusRowsInfinite } from "@/hooks/queries";
import type { CorpusField, CorpusWhere } from "@/lib/types";
import { CORPUS_ASSET_TYPES } from "@/lib/types";
import { QueryState } from "@/components/common/query-state";
import {
  AssetRecordSheet,
  type AssetRecord,
  type CorpusRow,
  type RecordDrill,
} from "@/components/corpus/asset-record-sheet";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** Rows per request. Larger than the old pager's 50 because a page is no longer something the
 * reader asks for — it is a refill that has to arrive before they scroll into the gap. 100 rows
 * of `column` measured ~90 KB. */
const PAGE = 100;

/** Fetch the next page when the last rendered row is within this many of the end. At ~52px a
 * row that is roughly a screen of lead time, which is what keeps a fast scroll from stalling on
 * an empty tail. */
const PREFETCH_ROWS = 20;

/** Row-height estimate for the virtualizer, in px. Real heights are measured after mount (cells
 * clamp to three lines, so a row is 1–3 lines tall); this only has to be close enough that the
 * scrollbar does not jump much while measuring. */
const ROW_ESTIMATE = 52;

/** A column's width, in px, **derived from its kind** — the same principle as `Cell` below.
 *
 * Widths have to be declared now, because the grid is `table-layout: fixed`. That in turn is
 * required by virtualization: under `auto`, a browser sizes columns from the cells it has, and
 * with only ~30 rows mounted at a time those are a *different* 30 cells after every scroll, so
 * every column would twitch as you moved. Fixed widths also mean the layout no longer costs a
 * pass over every row, which is most of why this stays smooth at 5,947 rows. */
function widthOf(column: CorpusField): number {
  switch (column.kind) {
    case "boolean":
      return 96;
    case "number":
      return 112;
    case "enum":
      return 160;
    case "ref":
      return 240;
    case "list":
      return 320;
    default:
      return 320;
  }
}

/** The "no scope" option value. Radix `Select` reserves the empty string for "nothing
 * selected" and throws on an item that uses it, so the sentinel has to be a real string. */
const ANY = "__any__";

/** Operators that take no value — the input is hidden and `value` carries the flag. */
const NO_VALUE_OPS = new Set(["present"]);

/** Whether a column is shown before anyone touches the column menu.
 *
 * A `metric` row is ten columns and 3421px wide against a 1100px container, so *something* has
 * to be off by default or the first thing every reader does is scroll sideways past columns
 * they did not want. The two rules are derived from the descriptor, not from a per-type list:
 *
 *   - **`block` fields are metadata about the asset, not the asset.** `governance`, `audit` and
 *     `reliability` are provenance blocks whose only filter operator is `present` — the engine
 *     itself says there is nothing in them to match on.
 *   - **`body` is the long twin of `summary`.** Both are shown as a three-line clamp, so the
 *     wide one costs 418px to show a truncated version of text the narrow one already covers.
 *
 * Hidden, never dropped: every column is one click away in the column menu, and the menu shows
 * the count so "10 of 10" and "6 of 10" are different-looking states. A column silently absent
 * from a table that claims to render every field would be the drift this file exists to avoid.
 */
function shownByDefault(column: CorpusField): boolean {
  return column.kind !== "block" && column.name !== "body";
}

/** The engine's operator names, in English.
 *
 * The wire vocabulary — `eq`, `neq`, `gte`, `len_gte`, `present` — is the engine's and stays
 * the engine's: `api/browse.py` parses `field:op:value` and the UI must send exactly what the
 * server will apply. But it was also what the *dropdown* showed, so a reader who does not write
 * SQL was asked to pick between "neq" and "present" to answer "which metrics mention gender".
 *
 * The mapping depends on `kind` as well as on the operator, because one wire word means two
 * things: `contains` on a string is a substring, and on a list it is membership. A single label
 * table keyed on the operator alone would have to pick one and be wrong for the other.
 */
function opLabel(op: string, kind: CorpusField["kind"]): string {
  if (kind === "list") {
    switch (op) {
      case "contains":
        return "includes";
      case "len_gte":
        return "has at least";
      case "len_lte":
        return "has at most";
    }
  }
  switch (op) {
    case "contains":
      return "contains";
    case "eq":
      return kind === "number" ? "equals" : "is";
    case "neq":
      return "is not";
    case "one_of":
      return "is one of";
    case "gte":
      return "is at least";
    case "lte":
      return "is at most";
    case "present":
      return "is filled in";
    default:
      // An operator the engine grew and this table has not learned yet. Showing the wire word
      // is worse than a label but far better than hiding an operator the server would accept.
      return op;
  }
}

/** A chip's operator, in the same English the popover shows.
 *
 * Falls back to the wire word only while the descriptor is still loading — a chip that said
 * `neq` after the dropdown said "is not" would undo the point of translating either.
 */
function chipOp(where: CorpusWhere, column: CorpusField | undefined): string {
  return column ? opLabel(where.op, column.kind) : where.op;
}

/** Where a given asset type keeps its schema and its table, **derived from the descriptor**.
 *
 * Filtering by schema and then by table is the most common thing anyone does here, and it was
 * only reachable by knowing that a metric keeps its table in `base_table`, a column in
 * `parent_table` and a join in `left_table`. That is the engine's business, not the reader's.
 *
 * Derived rather than hardcoded per type, because this file's whole premise is that a field
 * added to `corpus/schema.py` shows up here with no edit — a hand-written per-type map would be
 * the drift this component exists to avoid, and it would drift silently. The rules:
 *
 *   - the schema is the field literally named `schema`, when the type has one;
 *   - the table is the first `ref` field whose name mentions a table.
 *
 * Two types need a word. `table` rows *are* tables, so they have no table ref and scope on their
 * own `id`. `metric`, `term` and `join` have no `schema` field at all — but their table ref is
 * fully qualified (`address.zip_data`), so a schema scope is a `contains` on the prefix. That is
 * why `schemaVia` exists: the same user-facing control reaches two different engine shapes.
 */
function scopeOf(columns: CorpusField[], type: string) {
  const names = new Set(columns.map((c) => c.name));
  const tableRef = columns.find((c) => c.kind === "ref" && c.name.includes("table"));
  const tableField = tableRef?.name ?? (type === "table" && names.has("id") ? "id" : undefined);
  return {
    tableField,
    schemaField: names.has("schema") ? "schema" : undefined,
    /** How to express "in this schema" when the type has no `schema` field. */
    schemaVia: names.has("schema") ? null : tableField,
  };
}

export function AssetTable({
  focus,
}: {
  /** An asset located on the Search tab, as this table's **initial** state: its type selected
   * and an `id eq` predicate applied, so the hand-off lands on one row rather than on a type.
   *
   * Initial, not live: the page keys this component on it, so a hand-off is a fresh mount. A
   * live prop would need an effect to copy it into state, and an effect that overwrites the
   * filters a reader has since typed is a bug that only shows up in use. */
  focus?: { type: string; id: string } | null;
}) {
  const [type, setType] = useState<string>(focus?.type ?? "table");
  const [filters, setFilters] = useState<Record<string, CorpusWhere>>(
    // `id` is every asset's key (ADR 0008 D1) and `eq` is in its operator list, so this is a
    // predicate the server will apply — and if a type ever lacks it, the `unknown_where`
    // warning below says so rather than quietly showing the whole type.
    focus ? { id: { field: "id", op: "eq", value: focus.id } } : {},
  );
  const [sort, setSort] = useState<string | null>(null);
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  //: The hierarchy the corpus actually has — schema, then table — held apart from the
  //: per-column filters because it is the axis nearly every visit uses and because it has to
  //: survive a change of asset type. Switching from `metric` to `column` while scoped to
  //: `address.zip_data` should keep you there; a filter keyed on `base_table` could not,
  //: since `column` has no such field.
  const [schema, setSchema] = useState<string | null>(null);
  //: Columns the reader has explicitly toggled, per asset type. `undefined` for a column means
  //: "whatever `shownByDefault` says", so a new engine field appears without anyone opting in,
  //: and a reader's choice is not silently reverted when the descriptor changes around it.
  const [columnOverrides, setColumnOverrides] = useState<Record<string, Record<string, boolean>>>({});
  const [table, setTable] = useState<string | null>(null);
  //: The open record: the row object itself, plus the descriptors it is read through and the
  //: drill it offers. The row rather than its id because `/corpus/rows` already returned every
  //: field of it, and fetching one row to show what is in hand is a request for an answer we
  //: have. Kept after the sheet closes (`sheetOpen` is what closes) so the slide-out animates
  //: with its contents intact — see `AssetRecord`.
  const [record, setRecord] = useState<AssetRecord | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  const fields = useCorpusFields(type);

  // The two scope pickers read the corpus through the same route as the table itself, rather
  // than `/schema/summary`, which carries every table's full column list — 656 tables' worth
  // of payload to populate a dropdown.
  const schemaRows = useCorpusRows({ type: "schema", where: [], sort: "id", order: "asc", offset: 0, limit: 200 });
  const tableRows = useCorpusRows({
    type: "table",
    where: schema ? [{ field: "schema", op: "eq", value: schema }] : [],
    sort: "id",
    order: "asc",
    offset: 0,
    limit: 500,
  });
  const schemaOptions = useMemo(
    () => (schemaRows.data?.rows ?? []).map((r) => String(r.id ?? "")).filter(Boolean).sort(),
    [schemaRows.data],
  );
  const tableOptions = useMemo(
    () => (tableRows.data?.rows ?? []).map((r) => String(r.id ?? "")).filter(Boolean).sort(),
    [tableRows.data],
  );

  const allColumns = useMemo(() => fields.data?.columns ?? [], [fields.data]);
  // Memoized, and the `?? {}` is why: a type with no overrides yet produced a **fresh empty
  // object every render**, so `visibleColumns` below recomputed on every keystroke in a filter
  // even though nothing about the columns had changed.
  const overrides = useMemo(() => columnOverrides[type] ?? {}, [columnOverrides, type]);
  const visibleColumns = useMemo(
    () => allColumns.filter((c) => overrides[c.name] ?? shownByDefault(c)),
    [allColumns, overrides],
  );

  const scope = useMemo(
    () => scopeOf(fields.data?.columns ?? [], type),
    [fields.data, type],
  );

  // Only complete predicates are sent. A half-typed filter (`op` chosen, no value) would
  // otherwise be serialised as an empty match and silently return nothing.
  /** The per-column predicates, and **only** those.
   *
   * Kept separate from the scope because the chip row is built from it and each chip's `×`
   * removes by key from `filters`. When the two were one list, the scope's predicate got a chip
   * whose remove button deleted a `filters` entry that had never existed — the chip regenerated
   * on the next render and the button looked broken. A control that removes a thing must own
   * the state that produces it.
   */
  const columnFilters = useMemo(
    () =>
      Object.values(filters).filter(
        (w) => w.field && w.op && (NO_VALUE_OPS.has(w.op) || w.value !== ""),
      ),
    [filters],
  );

  const where = useMemo(() => {
    const scoped: CorpusWhere[] = [];
    // The table scope wins over the schema scope when both are set — a table id already names
    // its schema, so sending both would be one redundant predicate on every request.
    if (table && scope.tableField) {
      scoped.push({ field: scope.tableField, op: "eq", value: table });
    } else if (schema) {
      if (scope.schemaField) {
        scoped.push({ field: scope.schemaField, op: "eq", value: schema });
      } else if (scope.schemaVia) {
        // No `schema` field on this type; its table ref is qualified, so the prefix is the
        // schema. The trailing dot is defensive rather than demonstrated — no two schemas in
        // the current corpus share a prefix, so a bare `address` happens to be exact here —
        // but `address` matching an `address_book` is a bug nobody would see, because the
        // extra rows look like rows that belong.
        scoped.push({ field: scope.schemaVia, op: "contains", value: `${schema}.` });
      }
    }
    return [...scoped, ...columnFilters];
  }, [columnFilters, schema, table, scope]);

  const rows = useCorpusRowsInfinite({
    type,
    where,
    sort: sort ?? undefined,
    order,
    limit: PAGE,
  });

  /** Every loaded page, end to end. Memoized on the page array, so it is rebuilt once per
   * arriving page and not on every keystroke or hover. */
  const loaded = useMemo(
    () => (rows.data?.pages ?? []).flatMap((page) => page.rows),
    [rows.data],
  );
  //: Every page reports the same post-filter `total` and the same `unknown_where`; read them
  //: off the first, which is the one that cannot be missing while rows exist.
  const head = rows.data?.pages[0];
  const total = head?.total ?? 0;

  /* ── the scroller ──────────────────────────────────────────────────────────
   *
   * **Only the rows in view are mounted.** Without this, "no more paging" would mean a DOM
   * that grows by 100 `<tr>`s — each with up to twelve clamped cells — every time the reader
   * reaches the bottom, and a `column` type would end at 5,947 rows and ~70k cells. Scrolling
   * a list that long is not slow because of the data; it is slow because of the nodes.
   */
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: loaded.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_ESTIMATE,
    overscan: 12,
  });
  const virtualRows = virtualizer.getVirtualItems();

  const { fetchNextPage, hasNextPage, isFetchingNextPage } = rows;
  const lastVisible = virtualRows.length ? virtualRows[virtualRows.length - 1].index : -1;
  //: Refill on approach, not on arrival. Asking at the very last row means the reader hits the
  //: end of the list and waits; `PREFETCH_ROWS` of lead time means the next page is usually
  //: already there. React Query dedupes, so an extra call while one is in flight costs nothing
  //: — but `isFetchingNextPage` is in the condition anyway, to keep the *effect* from looping.
  useEffect(() => {
    if (lastVisible < 0 || !hasNextPage || isFetchingNextPage) return;
    if (lastVisible >= loaded.length - PREFETCH_ROWS) void fetchNextPage();
  }, [lastVisible, loaded.length, hasNextPage, isFetchingNextPage, fetchNextPage]);

  //: Re-aiming the grid puts you back at the top of it. The scroll box does not know the rows
  //: under it changed, so a filter typed 3,000 rows down would otherwise leave the reader at a
  //: scroll offset into a list that no longer has one.
  const queryKey = `${type}|${sort}|${order}|${where.map((w) => `${w.field}:${w.op}:${w.value}`).join("|")}`;
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [queryKey]);

  /** Whether anything is narrowing or reordering these rows — what `Clear filters` undoes.
   * Sort is included: it is not a predicate, but a reset that left the table sorted by a column
   * the reader can no longer see mentioned anywhere is a clear that did not clear. */
  const narrowed = columnFilters.length > 0 || schema !== null || table !== null || sort !== null;

  /** Re-aim the table: a type, a scope, and nothing carried over.
   *
   * **Every piece of narrowing state is listed here, once.** It was spread across two
   * near-identical resets before, and the copy that forgot `setSchema` left a schema scope
   * applied after "clear all" — silently, because the request and the chip row are both built
   * from the union and neither can tell what a reset forgot. A per-type filter cannot survive a
   * type change either: `base_table` means nothing to a `column`.
   */
  const pivot = (nextType: string, scope: { schema: string | null; table: string | null }) => {
    setType(nextType);
    setFilters({});
    setSchema(scope.schema);
    setTable(scope.table);
    setSort(null);
    // No `setOffset` here any more, and nothing replaces it: paging state is gone, and the
    // scroll position resets itself off the query signature — see the effect above. This reset
    // used to be the place a forgotten line silently left the old scope applied.
    setSheetOpen(false);
  };

  const reset = (nextType?: string) => pivot(nextType ?? type, { schema: null, table: null });

  /** Where a record can take you: **down the corpus's own hierarchy**, in this same browser.
   *
   * A schema's tables and a table's columns are not other pages — they are this page with
   * another type and another scope, which is exactly what the two selects above express. So the
   * drill sets them instead of opening a second list, and there is no second way to enumerate
   * assets to keep in step with this one. (It is also what replaced the graph sheet's lazily
   * fetched column table: same answer, one fewer route, and every column filterable.) */
  const drillFor = (row: CorpusRow): RecordDrill | null => {
    const id = String(row.id ?? "");
    if (!id) return null;
    if (type === "schema") {
      return {
        label: "Show this schema's tables",
        onDrill: () => pivot("table", { schema: id, table: null }),
      };
    }
    if (type === "table") {
      return {
        label: "Show this table's columns",
        onDrill: () =>
          pivot("column", {
            // The schema as well as the table: the table select's options are fetched per
            // schema, so a table scope with no schema behind it would show a chosen table the
            // dropdown could not list.
            schema: row.schema ? String(row.schema) : null,
            table: id,
          }),
      };
    }
    return null;
  };

  /** Open a row's record, freezing what it is read through at this moment. */
  const openRecord = (row: CorpusRow) => {
    setRecord({ row, type, columns: allColumns, drill: drillFor(row) });
    setSheetOpen(true);
  };

  /** The same thing, behind a callback whose identity never changes.
   *
   * `GridRow` is `memo`'d, and a fresh `onOpen` on every render would defeat that entirely —
   * every mounted row would re-render on every scroll frame, which is most of the work
   * virtualizing was supposed to remove. (React Compiler skips this component: `useVirtualizer`
   * is on its incompatible-library list, so nothing here is memoized for us.) The ref is
   * updated in an effect rather than during render, so the closure a row calls is always the
   * one from the last committed render. */
  const openRef = useRef(openRecord);
  useEffect(() => {
    openRef.current = openRecord;
  });
  const onOpen = useCallback((row: CorpusRow) => openRef.current(row), []);

  /** The descriptor for a filtered field, so a chip can speak the same English the popover
   * does. `undefined` while `/corpus/fields` is still in flight, which `chipOp` handles. */
  const descriptorFor = (name: string) => fields.data?.columns.find((c) => c.name === name);

  const setFilter = (column: CorpusField, patch: Partial<CorpusWhere>) => {
    setFilters((prev) => {
      const current = prev[column.name] ?? {
        field: column.name,
        op: column.ops[0],
        value: "",
      };
      const next = { ...current, ...patch };
      // Clearing the value on a value-taking operator removes the filter entirely, so the
      // predicate count in the footer matches what the user can see.
      if (!NO_VALUE_OPS.has(next.op) && next.value === "") {
        const rest = { ...prev };
        delete rest[column.name];
        return rest;
      }
      return { ...prev, [column.name]: next };
    });
  };

  return (
    // `h-full` + `min-h-0`: this fills the height the page hands it and gives all of the slack to
    // the grid below, which is the only part of this surface that scrolls.
    <div className="flex h-full min-h-0 flex-col gap-3">
      {/* Asset types come from the **engine's** register (`fields.types`), not from a
          constant here. `CORPUS_ASSET_TYPES` still listed `note`, which ADR 0005 §1.4
          deleted — so the UI offered a tab for a type the engine has never heard of, and it
          rendered an empty table that reads as "no notes" rather than "no such type". The
          constant is the pre-flight fallback only, for the first paint before `/corpus/fields`
          answers. */}
      <div className="flex flex-wrap items-center gap-1.5">
        {(fields.data?.types?.length ? fields.data.types : [...CORPUS_ASSET_TYPES]).map((t) => (
          <Button
            key={t}
            variant={t === type ? "secondary" : "ghost"}
            size="sm"
            aria-pressed={t === type}
            className={cn("font-mono", t !== type && "text-muted-foreground")}
            onClick={() => reset(t)}
          >
            {t}
          </Button>
        ))}
      </div>

      {/* **Schema, then table.** The corpus is hierarchical and this is the axis almost every
          visit uses, so it is a first-class control rather than two of the twelve per-column
          popovers. Choosing a schema narrows the table list to that schema, which is the
          hierarchy doing visible work: 656 tables is not a dropdown anyone can use, and the
          ~12 in one schema is.

          Read as a sentence — `in <schema> › <table>` — because two unlabelled dropdowns in a
          row do not say which narrows which, and the order is the whole point. What is on the
          right of this row changes the *view* rather than the rows: which columns are drawn, and
          the one button that puts everything back. Scope on the left, presentation on the
          right; they were interleaved, with a conditional sentence between them that moved the
          column menu whenever the asset type changed. */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">in</span>
        <Select
          value={schema ?? ANY}
          onValueChange={(next) => {
            setSchema(next === ANY ? null : next);
            // A table from the old schema is not in the new one, so keeping it would show an
            // empty table and no clue why.
            setTable(null);
          }}
        >
          <SelectTrigger size="sm" className="w-56" aria-label="Schema">
            <SelectValue placeholder="All schemas" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>All schemas</SelectItem>
            {schemaOptions.map((s) => (
              <SelectItem key={s} value={s} className="font-mono">
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <span className="text-muted-foreground" aria-hidden>
          ›
        </span>

        <Select
          value={table ?? ANY}
          disabled={!scope.tableField || !schema}
          onValueChange={(next) => {
            setTable(next === ANY ? null : next);
          }}
        >
          <SelectTrigger size="sm" className="w-64" aria-label="Table">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {/* The label, not a `placeholder`. A `SelectValue` placeholder only shows while
                nothing is selected, and `ANY` *is* a selection — so the disabled control read
                "All tables" and gave no hint that a schema comes first.

                It also says why it is disabled when the *type* is the reason. That used to be a
                sentence beside the control — "term assets are not attached to a table" — which
                said the right thing in the wrong place: it appeared and vanished with the asset
                type, shoving the column menu sideways each time, and left the disabled dropdown
                next to it reading "All tables". A control explains its own state. */}
            <SelectItem value={ANY}>
              {!scope.tableField
                ? `No table on ${type}`
                : schema
                  ? "All tables"
                  : "Pick a schema first"}
            </SelectItem>
            {tableOptions.map((t) => (
              <SelectItem key={t} value={t} className="font-mono">
                {t}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <div className="ml-auto flex items-center gap-1">
          {/* One button for "put it back", and it lives beside the view controls rather than at
              the end of the chip row below. On the chip row it dangled: a scope with no column
              filters rendered a row containing nothing but a `clear all` link, which looks like
              a control that lost its subject. */}
          {narrowed && (
            <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={() => reset()}>
              <X />
              Clear filters
            </Button>
          )}

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm">
                <Columns3 />
                Columns
                {/* The count, always, not only when something is hidden. "6 of 10" and "10 of
                    10" have to look different at a glance, or a reader cannot tell a table that
                    is hiding something from one that is not — which is the only real risk this
                    control introduces. */}
                <span className="text-muted-foreground">
                  {visibleColumns.length} of {allColumns.length}
                </span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="max-h-96 w-56 overflow-y-auto">
              <DropdownMenuLabel>Columns</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {allColumns.map((c) => (
                <DropdownMenuCheckboxItem
                  key={c.name}
                  className="font-mono text-xs"
                  checked={overrides[c.name] ?? shownByDefault(c)}
                  // Radix closes a menu on select; a column menu is used in runs, so keep it open.
                  onSelect={(e) => e.preventDefault()}
                  onCheckedChange={(next) =>
                    setColumnOverrides((prev) => ({
                      ...prev,
                      [type]: { ...(prev[type] ?? {}), [c.name]: next },
                    }))
                  }
                >
                  {c.name}
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* The active filters, spelled out and individually removable. This is what pays for
          moving the controls into a per-column popover: the *state* stays on screen even
          though the *controls* do not, so a filtered table can never look like an unfiltered
          one — which is the same honesty the footer's post-filter `total` exists for. */}
      {columnFilters.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {/* Only the per-column filters get a chip. The schema and table scope is already
              spelled out in the two selects above and can be cleared there, so a chip for it
              would duplicate a visible control — and duplicating it is exactly what produced a
              remove button with no state behind it. */}
          {columnFilters.map((w) => (
            <Badge key={w.field} variant="secondary" className="gap-1 py-0.5 pr-0.5 font-mono">
              <span className="text-muted-foreground">{w.field}</span>
              <span className="text-muted-foreground">{chipOp(w, descriptorFor(w.field))}</span>
              {!NO_VALUE_OPS.has(w.op) && <span className="max-w-40 truncate">{w.value}</span>}
              {NO_VALUE_OPS.has(w.op) && <span>{w.value === "true" ? "yes" : "no"}</span>}
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={`Remove ${w.field} filter`}
                onClick={() =>
                  setFilters((prev) => {
                    const rest = { ...prev };
                    delete rest[w.field];
                    return rest;
                  })
                }
              >
                <X />
              </Button>
            </Badge>
          ))}
        </div>
      )}

      <QueryState
        query={fields}
        isEmpty={(data) => data.columns.length === 0}
        emptyMessage={`No filterable columns for ${type}.`}
        skeleton={<Skeleton className="h-64 w-full" />}
      >
        {() => (
          <>
            {head?.unknown_where?.length ? (
              <div className="flex shrink-0 items-start gap-2 rounded-md border border-tier-refused/40 bg-tier-refused/5 px-3 py-2 text-xs">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-tier-refused" />
                <div>
                  <p className="font-medium">
                    {head.unknown_where.length} filter(s) were not applied
                  </p>
                  <p className="font-mono text-xs text-muted-foreground">
                    {head.unknown_where.join(" · ")}
                  </p>
                </div>
              </div>
            ) : null}

            {/* **The rows scroll, the page does not.** The border box takes every pixel the page
                has left (`flex-1`), and the table's own container is the scroll box in both
                directions — one container, so the sticky header below has something that
                actually scrolls to stick to. */}
            <div className="flex min-h-0 flex-1 flex-col rounded-md border">
              <Table
                containerRef={scrollRef}
                containerClassName="min-h-0 flex-1 overflow-auto"
                // `table-fixed` + the `colgroup` below. See `widthOf`: virtualization and
                // content-derived column widths cannot both be true.
                className="table-fixed"
              >
                <colgroup>
                  {visibleColumns.map((column) => (
                    <col key={column.name} style={{ width: widthOf(column) }} />
                  ))}
                </colgroup>
                {/* Sticky, and opaque: rows pass *under* the header, and a transparent header
                    would let them show through. The bottom rule is an inset shadow rather than a
                    border because Tailwind's preflight collapses table borders, and a collapsed
                    border belongs to the table's border box — so it stays behind at the top of
                    the scroll box instead of travelling with the sticky header. */}
                <TableHeader className="sticky top-0 z-20 bg-background [&_tr]:border-b-0 [&_th]:shadow-[inset_0_-1px_0_var(--border)]">
                  <TableRow>
                    {visibleColumns.map((column) => (
                      <TableHead key={column.name} className="align-top">
                        <ColumnHeader
                          column={column}
                          sort={sort}
                          order={order}
                          filter={filters[column.name]}
                          onSort={() => {
                            if (!column.sortable) return;
                            if (sort === column.name) {
                              setOrder(order === "asc" ? "desc" : "asc");
                            } else {
                              setSort(column.name);
                              setOrder("asc");
                            }
                          }}
                          onFilter={(patch) => setFilter(column, patch)}
                        />
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {/* An empty body under a full header row reads as a table still loading, or
                      as a filter that broke. The count line says `0 matching` at the same time,
                      but it says it 40px below the place a reader is looking. */}
                  {rows.data && loaded.length === 0 && (
                    <TableRow>
                      <TableCell
                        colSpan={Math.max(1, visibleColumns.length)}
                        className="py-10 text-center text-sm text-muted-foreground"
                      >
                        {narrowed
                          ? `No ${type} assets match these filters.`
                          : `This corpus has no ${type} assets.`}
                      </TableCell>
                    </TableRow>
                  )}
                  {/* Two spacer rows stand in for everything above and below the window.
                      Absolute positioning — the usual virtualizer trick — would take the rows
                      out of the table's layout and cost every cell its column, so the rows stay
                      real `<tr>`s and the *scroll height* is what gets faked. */}
                  {/* The spacer carries a `td`, because a `tr` with no cells has no height to
                      set — the row box would collapse and the scroll height with it. */}
                  {virtualRows.length > 0 && virtualRows[0].start > 0 && (
                    <tr aria-hidden>
                      <td
                        colSpan={Math.max(1, visibleColumns.length)}
                        style={{ height: virtualRows[0].start, padding: 0 }}
                      />
                    </tr>
                  )}
                  {virtualRows.map((virtualRow) => (
                    <GridRow
                      key={String(loaded[virtualRow.index].id ?? virtualRow.index)}
                      index={virtualRow.index}
                      row={loaded[virtualRow.index]}
                      columns={visibleColumns}
                      measure={virtualizer.measureElement}
                      onOpen={onOpen}
                    />
                  ))}
                  {virtualRows.length > 0 && (
                    <tr aria-hidden>
                      <td
                        colSpan={Math.max(1, visibleColumns.length)}
                        style={{
                          height: Math.max(
                            0,
                            virtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end,
                          ),
                          padding: 0,
                        }}
                      />
                    </tr>
                  )}
                </TableBody>
              </Table>
            </div>

            <Footer
              total={total}
              loadedCount={loaded.length}
              filtered={where.length > 0}
              loadingMore={rows.isFetchingNextPage}
              refreshing={rows.isFetching && !rows.isFetchingNextPage}
            />
          </>
        )}
      </QueryState>

      <AssetRecordSheet record={record} open={sheetOpen} onOpenChange={setSheetOpen} />
    </div>
  );
}

/** One row of the grid, **memoized**, which is the second half of making this scroll.
 *
 * Virtualizing bounds how many rows are *mounted*; it does nothing about how many *re-render*.
 * Every scroll frame changes the virtualizer's state and so re-renders `AssetTable`, and
 * without this every mounted row — ~30, at up to twelve cells each — would re-render with it.
 * All four props are stable across a scroll (`loaded` and `visibleColumns` are memoized, the
 * row objects come straight out of the query cache, `onOpen` is ref-backed), so a row re-renders
 * only when it actually enters the window or its data changes.
 */
const GridRow = memo(function GridRow({
  index,
  row,
  columns,
  measure,
  onOpen,
}: {
  index: number;
  row: CorpusRow;
  columns: CorpusField[];
  measure: (node: Element | null) => void;
  onOpen: (row: CorpusRow) => void;
}) {
  return (
    <TableRow
      data-index={index}
      // Measured, not assumed: a row is one to three lines tall depending on its longest
      // clamped cell, and `ROW_ESTIMATE` is only the opening guess.
      ref={measure}
      className="cursor-pointer"
      // A `tr` is not focusable and Radix has no opinion here, so the keyboard path is spelled
      // out: every row opens a record, and a row a mouse can open is a row a keyboard has to be
      // able to open.
      tabIndex={0}
      onClick={() => onOpen(row)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(row);
        }
      }}
    >
      {columns.map((column) => (
        <TableCell key={column.name} className="align-top">
          {/* `break-words` because a corpus id like
              `metric_book_publishing_company_average_order_quantity` has no space to wrap at, so
              the column's width has nothing to act on without it. The width itself comes from
              the `colgroup`: a `max-width` on a cell was advisory under `table-layout: auto` and
              the browser ignored it — measured at 418px while the class said 22rem. */}
          <div className="break-words">
            <Cell value={row[column.name]} kind={column.kind} />
          </div>
        </TableCell>
      ))}
    </TableRow>
  );
});

/** One header: the name, a sort toggle, and the filter control for its kind. */
function ColumnHeader({
  column,
  sort,
  order,
  filter,
  onSort,
  onFilter,
}: {
  column: CorpusField;
  sort: string | null;
  order: "asc" | "desc";
  filter?: CorpusWhere;
  onSort: () => void;
  onFilter: (patch: Partial<CorpusWhere>) => void;
}) {
  const active = sort === column.name;
  const op = filter?.op ?? column.ops[0];
  const filtered = filter !== undefined;
  return (
    <div className="flex items-center gap-1 py-1">
      <button
        type="button"
        onClick={onSort}
        disabled={!column.sortable}
        className={cn(
          "flex min-w-0 items-center gap-1.5 font-mono text-sm",
          column.sortable ? "hover:text-foreground" : "cursor-default opacity-70",
          active && "text-foreground",
        )}
        title={column.sortable ? "Sort" : "Not sortable"}
      >
        <span className="truncate">{column.name}</span>
        {active &&
          (order === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
      </button>

      {/* **The filter moved out of the header and behind this button.** It used to sit inline:
          an operator `<select>` plus an input, in *every* column, always. That made the header
          three rows tall, put two native OS-styled dropdowns per column on the page, and gave
          the heaviest visual weight on the screen to a control almost always in its default
          state. A column is filtered rarely; the header is read constantly.

          Nothing is hidden by this. An engaged filter marks its column here and is also listed
          as a removable chip above the table, so "which filters are on" is answerable without
          opening anything — which is the property the always-visible version really provided
          and the one worth keeping. */}
      <Popover>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="icon-xs"
            className={cn(
              "ml-auto shrink-0",
              filtered ? "text-foreground" : "text-muted-foreground/60",
            )}
            aria-label={`Filter ${column.name}`}
          >
            <ListFilter />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="flex w-60 flex-col gap-2 p-3">
          <p className="font-mono text-sm text-muted-foreground">{column.name}</p>
          <div className="flex items-center gap-2">
            {/* Operators come from the server, so the UI can only offer what will be applied. */}
            <Select value={op} onValueChange={(next) => onFilter({ op: next })}>
              <SelectTrigger size="sm" className="w-28" aria-label={`${column.name} operator`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {column.ops.map((o) => (
                  <SelectItem key={o} value={o}>
                    {opLabel(o, column.kind)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {NO_VALUE_OPS.has(op) ? (
              <Select
                value={filter?.value ? String(filter.value) : "any"}
                onValueChange={(next) => onFilter({ op, value: next === "any" ? "" : next })}
              >
                <SelectTrigger size="sm" className="flex-1" aria-label={`${column.name} present`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {/* A real value, never "". Radix Select reserves the empty string for
                      "nothing selected" and throws on an item that uses it. */}
                  <SelectItem value="any">either</SelectItem>
                  <SelectItem value="true">yes</SelectItem>
                  <SelectItem value="false">no</SelectItem>
                </SelectContent>
              </Select>
            ) : (
              <Input
                autoFocus
                value={filter?.value ?? ""}
                onChange={(e) => onFilter({ op, value: e.target.value })}
                placeholder="value"
                className="h-8 flex-1"
                aria-label={`${column.name} filter`}
              />
            )}
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}

/** A value, rendered by kind rather than by field name.
 *
 * Deliberately not per-field renderers: the shape belongs to the engine's asset schema, and
 * an opinion about it here would be a second definition that drifts. `kind` is the one thing
 * the server told us about it. */
function Cell({ value, kind }: { value: unknown; kind: CorpusField["kind"] }) {
  if (value === null || value === undefined || value === "") {
    return <span className="text-muted-foreground">—</span>;
  }
  if (Array.isArray(value)) {
    return (
      <span className="font-mono text-sm">
        <span className="text-muted-foreground">[{value.length}]</span>{" "}
        {value.slice(0, 3).map(String).join(", ")}
        {value.length > 3 ? " …" : ""}
      </span>
    );
  }
  if (typeof value === "boolean") {
    return <span className="font-mono text-sm">{value ? "true" : "false"}</span>;
  }
  const text = String(value);
  return (
    <span
      className={cn(
        "line-clamp-3 text-sm",
        kind === "number" || kind === "ref" || kind === "enum" ? "font-mono" : "",
      )}
      title={text.length > 120 ? text : undefined}
    >
      {text}
    </span>
  );
}

/** What is in the scroller, and how much of the answer it is.
 *
 * **Not a pager any more, and it still has to say `of {total}`.** Scrolling is now the only way
 * to reach row 3,000, so the one thing the old `1–50 of 656` said that the scrollbar cannot is
 * the part worth keeping: how many assets match. A scrollbar reports the *loaded* list, so
 * without this line a reader who has pulled 200 of 5,947 rows sees a scrollbar that looks
 * nearly full and concludes the corpus has 200 columns in it. */
function Footer({
  total,
  loadedCount,
  filtered,
  loadingMore,
  refreshing,
}: {
  total: number;
  loadedCount: number;
  filtered: boolean;
  loadingMore: boolean;
  refreshing: boolean;
}) {
  const complete = loadedCount >= total;
  return (
    <div className="flex shrink-0 items-center justify-between gap-3 text-sm text-muted-foreground">
      <span>
        {/* Once everything is loaded there is no "so far" to report, and printing
            `5,947 of 5,947` invites a reader to look for the difference. */}
        {complete ? total.toLocaleString() : `${loadedCount.toLocaleString()} of ${total.toLocaleString()}`}
        {/* Saying "matching" only when a filter is on: it is the difference between a
            filtered count and a corpus count, and the reader needs to know which. */}
        {filtered ? " matching" : ""}
      </span>
      <span className="font-mono text-xs">
        {loadingMore ? "loading more…" : refreshing ? "loading…" : ""}
      </span>
    </div>
  );
}
