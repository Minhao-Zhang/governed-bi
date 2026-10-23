"use client";

import { rowCaption, type ResultTable } from "@/lib/answer-parts";

/** The rows the turn kept. The caption appears only when more rows exist than are shown. */
export function ResultRows({ table }: { table: ResultTable }) {
  const caption = rowCaption(table);
  return (
    <div>
      <table className="border-collapse text-sm">
        <thead>
          <tr>
            {table.columns.map((column) => (
              <th
                key={column}
                scope="col"
                className="border-b border-[var(--bubble)] px-3 py-1 text-left font-medium text-[var(--muted)]"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {table.columns.map((column, columnIndex) => (
                <td key={column} className="px-3 py-1 text-left">
                  {row[columnIndex] == null ? "" : String(row[columnIndex])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {caption && <p className="mt-1 text-[var(--muted)]">{caption}</p>}
    </div>
  );
}
