"use client";

/**
 * One asset's whole record — what clicking a row in the corpus table now opens.
 *
 * It used to open `NodeDetailSheet`, which is the **graph's** panel: it resolves columns for a
 * `table` selection and, for everything else, shows `id / kind / label`. Three fields the row
 * you just clicked was already showing. So on this page every row but a table's was a
 * cursor-pointer that led nowhere, which is worse than a row that does not invite the click.
 *
 * What a corpus row actually has to offer is the part the table cannot show: the table clamps
 * cells to three lines and 280px and hides the `block` and `body` columns by default, because a
 * `metric` row is ten columns and 3421px wide. All of that is here, unclamped, in the field
 * order the engine declares.
 *
 * Two things are deliberate:
 *
 *   - **Every declared field appears, empty or not.** A field absent from a record and a field
 *     absent from this list look identical, so "—" is information and omission is not.
 *   - **Keys the descriptor does not declare get their own section.** That is the one signal
 *     that the engine has grown a field this app has not learned about — the same reason
 *     `/audit`'s trace carries `undeclared_keys` rather than silently dropping them.
 */

import { CornerDownRight } from "lucide-react";

import { cn } from "@/lib/utils";
import type { CorpusField } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

/** A row as `/corpus/rows` returns it: the declared fields plus `asset_type`. */
export type CorpusRow = Record<string, unknown>;

/** Where a row can take you, when the corpus's own hierarchy says there is a "down".
 *
 * Handled by the table rather than by a link, because "this schema's tables" is not a new
 * surface — it is this surface with a different type and scope. Reusing the browser for it means
 * the drill can only ever show what the browser can show, which is the point: no second way to
 * list assets, no second thing to keep in step. */
export type RecordDrill = { label: string; onDrill: () => void };

/** One row, with the descriptors and the drill it had **when it was opened**.
 *
 * Captured as one value on the click rather than read live from the table's state, and that is
 * not incidental. Radix keeps a sheet mounted for the length of its slide-out, so a panel wired
 * to live state re-renders while it is still on screen — and the drill changes the table's type
 * in the same tick it closes the sheet, which would redraw a *table*'s record against a
 * *column*'s field list: every field "—", for the length of the animation. A record is a row and
 * the schema it is read through; splitting them lets them disagree. */
export type AssetRecord = {
  row: CorpusRow;
  type: string;
  columns: CorpusField[];
  drill?: RecordDrill | null;
};

export function AssetRecordSheet({
  record,
  open,
  onOpenChange,
}: {
  record: AssetRecord | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        {record ? (
          <RecordBody {...record} />
        ) : (
          <SheetHeader>
            <SheetTitle>No asset selected</SheetTitle>
            <SheetDescription>Pick a row to see its record.</SheetDescription>
          </SheetHeader>
        )}
      </SheetContent>
    </Sheet>
  );
}

function RecordBody({ row, type, columns, drill }: AssetRecord) {
  const title = String(row.physical_name ?? row.name ?? row.id ?? "");
  const declared = new Set(columns.map((c) => c.name));
  // `asset_type` is the server's own tag on the row, and it is already the badge in the header.
  const undeclared = Object.keys(row).filter((k) => !declared.has(k) && k !== "asset_type");

  return (
    <>
      <SheetHeader>
        <SheetTitle className="flex items-center gap-2">
          <span className="min-w-0 truncate">{title}</span>
          <Badge variant="outline" className="shrink-0 font-mono">
            {String(row.asset_type ?? type)}
          </Badge>
        </SheetTitle>
        <SheetDescription>
          Every field the engine declares for this asset, in full.
        </SheetDescription>
      </SheetHeader>

      <div className="flex flex-col gap-4 px-4 pb-8">
        {drill && (
          <Button variant="outline" size="sm" className="self-start" onClick={drill.onDrill}>
            <CornerDownRight />
            {drill.label}
          </Button>
        )}

        <dl className="flex flex-col">
          {columns.map((column) => (
            <Row
              key={column.name}
              name={column.name}
              kind={column.kind}
              value={row[column.name]}
            />
          ))}
        </dl>

        {undeclared.length > 0 && (
          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium text-muted-foreground">
              Fields the engine sent that <code>/corpus/fields</code> does not declare
            </p>
            <dl className="flex flex-col">
              {undeclared.map((name) => (
                <Row key={name} name={name} kind="string" value={row[name]} />
              ))}
            </dl>
          </div>
        )}
      </div>
    </>
  );
}

/** One field. Two columns on wide sheets, stacked when the label column would squeeze the
 * value — a `sample_values` list has nothing to gain from 24rem of the 30rem available. */
function Row({
  name,
  kind,
  value,
}: {
  name: string;
  kind: CorpusField["kind"];
  value: unknown;
}) {
  return (
    <div className="grid grid-cols-[9rem_minmax(0,1fr)] gap-3 border-b py-2 last:border-b-0">
      <dt className="font-mono text-xs break-words text-muted-foreground">{name}</dt>
      <dd className="min-w-0 text-sm">
        <Value kind={kind} value={value} />
      </dd>
    </div>
  );
}

function Value({ kind, value }: { kind: CorpusField["kind"]; value: unknown }) {
  // A `block` arrives as the server's own flattened string — `"False   "`, `"curator draft
  // inference-1 …"` — so its runs of padding are an artefact of that flattening, not content.
  // Collapsed rather than reformatted: inventing structure the engine did not send would be a
  // second opinion about a shape that is the engine's.
  const collapsed =
    kind === "block" && typeof value === "string" ? value.replace(/\s+/g, " ").trim() : value;

  // An empty list is an empty field, and "0 entries" is a longer way of saying "—" that reads
  // like a count worth noticing. `table.rules` is `[]` on most tables.
  if (
    collapsed === null ||
    collapsed === undefined ||
    collapsed === "" ||
    (Array.isArray(collapsed) && collapsed.length === 0)
  ) {
    return <span className="text-muted-foreground">—</span>;
  }
  if (Array.isArray(collapsed)) {
    return (
      <div className="flex flex-col gap-0.5">
        <span className="text-xs text-muted-foreground">{collapsed.length} entries</span>
        <ul className="flex flex-col gap-0.5">
          {collapsed.map((item, i) => (
            <li key={i} className="font-mono text-xs break-words">
              {String(item)}
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (typeof collapsed === "boolean") {
    return <span className="font-mono text-sm">{collapsed ? "true" : "false"}</span>;
  }
  return (
    <span
      className={cn(
        // `whitespace-pre-wrap` because a `body` is prose the curator wrote with its own line
        // breaks, and the table's three-line clamp is exactly what this sheet exists to undo.
        "break-words whitespace-pre-wrap",
        kind === "number" || kind === "ref" || kind === "enum" || kind === "block"
          ? "font-mono text-xs"
          : "text-sm",
      )}
    >
      {String(collapsed)}
    </span>
  );
}
