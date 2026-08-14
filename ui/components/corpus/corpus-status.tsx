"use client";

/**
 * What the loaded corpus **is**, and what is wrong with it — as the page's status control
 * rather than a slab above its subject.
 *
 * This was a full-width panel at the top of `/corpus`, tinted green whenever the corpus was
 * fine. Three things were wrong with that, and they are the same thing three times: it spent
 * permanent vertical space and permanent colour on a state that is nominal on every visit. The
 * numbers it carried are read once a session; the table underneath is read continuously, and it
 * started 250px down the page. A green background is also alarm colouring used for "no alarm",
 * which leaves nothing louder for the case that matters.
 *
 * So: a quiet control in the page header, and a popover holding **every** number the route
 * reports — including `schema_tags` and `table_pairs_with_joins`, which the panel dropped for
 * want of room. Loud is reserved for earned loudness, and lives in `CorpusFatalNotice` below.
 *
 * `fatal` and `degradation` are never summed, here or anywhere. ADR 0008 D9 makes them
 * different states — the engine refuses to serve on the first and serves past the second — so a
 * single "problems" count would put this page and the CLI back into disagreement. They get
 * separate counters in the trigger, in separate colours, and separate lists inside.
 *
 * Was `corpus-state.tsx`, and before that the `/audit` page's top section plus the whole of the
 * deleted `/health` page (ADR 0007 Amendment 1). What stayed on `/audit` is what describes
 * **runs** rather than the corpus.
 */

import { AlertTriangle, ChevronDown, FileWarning } from "lucide-react";

import { cn } from "@/lib/utils";
import { useAuditCorpus } from "@/hooks/queries";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";

/** `3 degradations`, `1 degradation` — the plural is not worth a library and is worth getting
 * right, because these strings sit next to a number a reader is being asked to trust. */
function count(n: number, noun: string): string {
  return `${n.toLocaleString()} ${noun}${n === 1 ? "" : "s"}`;
}

export function CorpusStatus() {
  const { data: corpus, isPending, isError } = useAuditCorpus();

  // Deliberately not `QueryState`: its skeleton and error box are content-column furniture,
  // and a 16-row-tall dashed panel in a page header would be worse than either state it reports.
  if (isPending) return <Skeleton className="h-8 w-64" />;
  if (isError || !corpus) {
    return (
      <span className="flex items-center gap-2 text-xs text-muted-foreground">
        <span className="size-2 rounded-full bg-muted-foreground/50" aria-hidden />
        corpus state unavailable
      </span>
    );
  }

  const { assets, problems, structure, schemas, servable, corpus_content_hash } = corpus;
  const byType = Object.entries(assets.by_type).sort(([a], [b]) => a.localeCompare(b));

  return (
    <Popover>
      <PopoverTrigger asChild>
        {/* Wraps rather than truncates. The whole strip is ~395px, which does not fit a phone —
            and every part of it is a fact a reader would otherwise have to open this popover to
            get back, so two lines beats hiding one. Three classes are load-bearing: `shrink`
            and `min-w-0` undo the button base's `shrink-0` and a flex item's `min-width: auto`
            (both of which pin it to its max-content width, so it hung off the right edge instead
            of wrapping), and `h-auto` undoes `size="sm"`'s fixed 32px height. */}
        <Button
          variant="outline"
          size="sm"
          className="h-auto min-w-0 shrink flex-wrap justify-start gap-x-2 gap-y-0.5 py-1.5 text-left font-normal whitespace-normal"
        >
          <span
            className={cn(
              "size-2 shrink-0 rounded-full",
              servable ? "bg-tier-governed" : "bg-tier-refused",
            )}
            aria-hidden
          />
          <span className="font-medium">{servable ? "Servable" : "Not servable"}</span>
          <span className="text-muted-foreground">
            {assets.total.toLocaleString()} assets · {schemas.length} schemas
          </span>
          {problems.n_fatal > 0 && (
            <span className="text-tier-refused">{count(problems.n_fatal, "fatal problem")}</span>
          )}
          {problems.n_degradations > 0 && (
            <span className="text-tier-lineage">
              {count(problems.n_degradations, "degradation")}
            </span>
          )}
          <ChevronDown className="text-muted-foreground" />
        </Button>
      </PopoverTrigger>

      <PopoverContent
        align="end"
        className="max-h-[70vh] w-96 max-w-[calc(100vw-2rem)] overflow-y-auto"
      >
        <PopoverHeader>
          <PopoverTitle>{servable ? "Servable" : "Not servable"}</PopoverTitle>
          <PopoverDescription className="text-xs">
            {servable
              ? "The engine will answer over this corpus."
              : "The engine refuses to answer over this corpus."}
          </PopoverDescription>
        </PopoverHeader>

        {corpus_content_hash && (
          <div className="flex items-baseline justify-between gap-2 text-xs">
            <span className="text-muted-foreground">content hash</span>
            {/* Truncated with the whole thing on `title`: it identifies the corpus, and the
                identity is only useful when it is complete enough to compare. */}
            <span className="truncate font-mono" title={corpus_content_hash}>
              {corpus_content_hash.slice(0, 16)}…
            </span>
          </div>
        )}

        {/* Per-type counts live here and **only** here. Putting them on the type pills below
            was the obvious move and the wrong one: those pills sit above a table that is
            usually scoped to a schema, so `column 5,947` would sit above 87 rows and read as a
            filter that did nothing. The filtered count belongs to the footer, which has it. */}
        <Numbers title="Assets by type" rows={byType} mono />

        <Numbers
          title="Structure"
          rows={[
            ["join edges", structure.join_edges],
            ["table pairs with joins", structure.table_pairs_with_joins],
            ["references", structure.references],
            ["schema tags", structure.schema_tags],
            ["untagged assets", structure.untagged_assets],
          ]}
        />

        {problems.fatal.length > 0 && (
          <Problems
            title={count(problems.n_fatal, "fatal problem")}
            items={problems.fatal}
            tone="refused"
          />
        )}
        {problems.degradations.length > 0 && (
          <Problems
            title={`${count(problems.n_degradations, "degradation")} — recorded, counted, still servable`}
            items={problems.degradations}
            tone="lineage"
          />
        )}
      </PopoverContent>
    </Popover>
  );
}

/**
 * The one loud thing on this page, in the content column where it cannot be missed.
 *
 * Renders nothing at all when the corpus serves — which is what buys it the right to be a
 * red panel when it does render. A reader who sees this has a corpus the engine will not
 * answer over, so every fatal string is printed rather than collapsed behind a disclosure.
 */
export function CorpusFatalNotice() {
  const { data: corpus } = useAuditCorpus();
  if (!corpus || (corpus.servable && corpus.problems.n_fatal === 0)) return null;

  const { n_fatal, fatal } = corpus.problems;
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-tier-refused/40 bg-tier-refused/5 px-4 py-3">
      <p className="flex items-center gap-2 text-sm font-medium">
        <AlertTriangle className="size-4 shrink-0 text-tier-refused" />
        {n_fatal > 0
          ? `Not servable — ${count(n_fatal, "fatal problem")}: the corpus is not what it claims`
          : "Not servable"}
      </p>
      <ul className="flex flex-col gap-1">
        {fatal.map((problem) => (
          <li
            key={problem}
            className="rounded bg-background/60 px-2 py-1 font-mono text-xs leading-relaxed"
          >
            {problem}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A labelled column of `label … number` rows. Numbers right-aligned and `tabular-nums` so
 * they can be compared down the column, which is the only reason to list them together. */
function Numbers({
  title,
  rows,
  mono,
}: {
  title: string;
  rows: Array<[string, number]>;
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 text-xs">
        {rows.map(([label, n]) => (
          <div key={label} className="col-span-2 grid grid-cols-subgrid">
            <dt className={cn("truncate text-muted-foreground", mono && "font-mono")}>{label}</dt>
            <dd className="tabular-nums">{n.toLocaleString()}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** One problem list. Scrolls at four-ish entries: 3 degradations is a paragraph, and 300
 * would otherwise make this popover taller than the window it is anchored in. */
function Problems({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "refused" | "lineage";
}) {
  const Icon = tone === "refused" ? AlertTriangle : FileWarning;
  return (
    <div className="flex flex-col gap-1">
      <p className="flex items-center gap-1.5 text-xs font-medium">
        <Icon
          className={cn(
            "size-3.5 shrink-0",
            tone === "refused" ? "text-tier-refused" : "text-tier-lineage",
          )}
        />
        {title}
      </p>
      <ul className="flex max-h-48 flex-col gap-1 overflow-y-auto">
        {items.map((problem) => (
          <li
            key={problem}
            className="rounded bg-muted/50 px-2 py-1 font-mono text-[11px] leading-relaxed"
          >
            {problem}
          </li>
        ))}
      </ul>
    </div>
  );
}
