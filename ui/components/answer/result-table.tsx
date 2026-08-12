"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { ResultTable } from "@/lib/types";

function renderCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** A column reads as numeric when every non-null cell in it is a number, so we
 * can right-align it for scannability (nulls are ignored, not disqualifying). */
function numericColumns(rows: unknown[][], columnCount: number): boolean[] {
  return Array.from({ length: columnCount }, (_, col) => {
    let sawValue = false;
    for (const row of rows) {
      const cell = row[col];
      if (cell === null || cell === undefined) continue;
      if (typeof cell !== "number") return false;
      sawValue = true;
    }
    return sawValue;
  });
}

/** Collapsible result grid from `Answer.result` (columns/rows), with a
 * truncation note when the executed set was larger than the preview. */
export function ResultTable({ result }: { result: ResultTable }) {
  const [open, setOpen] = useState(true);
  const numeric = numericColumns(result.rows, result.columns.length);

  return (
    <div className="rounded-md border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-sm font-medium"
      >
        {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        Result
        <span className="text-muted-foreground">
          · {result.row_count.toLocaleString()} row{result.row_count === 1 ? "" : "s"}
          {result.truncated ? ` (showing ${result.rows.length})` : ""}
        </span>
      </button>

      {open && (
        <div className="max-h-80 overflow-auto border-t">
          <Table>
            <TableHeader>
              <TableRow>
                {result.columns.map((col, j) => (
                  <TableHead
                    key={col}
                    className={cn("font-mono text-xs", numeric[j] && "text-right")}
                  >
                    {col}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.rows.map((row, i) => (
                <TableRow key={i}>
                  {row.map((cell, j) => (
                    <TableCell
                      key={j}
                      className={cn(
                        "font-mono text-xs",
                        numeric[j] && "text-right tabular-nums",
                      )}
                    >
                      {renderCell(cell)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
