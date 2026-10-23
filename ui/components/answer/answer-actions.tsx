"use client";

import { useState } from "react";

import {
  answerAsPlainText,
  attemptsOfRecord,
  generatedSql,
  latencySeconds,
  layerChecks,
  modelsUsed,
  retrievedTables,
  type ResultTable,
} from "@/lib/answer-parts";

/** Copy and Details. Details opens under the row and shows only the sections the record has. */
export function AnswerActions({
  text,
  table,
  record,
}: {
  text: string | null;
  table: ResultTable | null;
  record: Record<string, unknown>;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <div className="flex gap-4">
        <button
          type="button"
          className="text-[var(--muted)]"
          onClick={() => {
            const plain = answerAsPlainText(text, table);
            void navigator.clipboard?.writeText(plain).catch(() => {});
          }}
        >
          Copy
        </button>
        <button
          type="button"
          className="text-[var(--muted)]"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          Details
        </button>
      </div>
      {open && <Details record={record} />}
    </div>
  );
}

function Details({ record }: { record: Record<string, unknown> }) {
  const sql = generatedSql(record);
  const checks = layerChecks(attemptsOfRecord(record));
  const tables = retrievedTables(record);
  const models = modelsUsed(record);
  const seconds = latencySeconds(record);
  return (
    <div data-details className="mt-3 space-y-3">
      {sql && (
        <section>
          <h3 className="text-[var(--muted)]">SQL</h3>
          <pre className="mt-1 max-h-48 overflow-auto font-mono text-xs whitespace-pre">{sql}</pre>
        </section>
      )}
      {checks && (
        <section>
          <h3 className="text-[var(--muted)]">Checks</h3>
          <ul className="mt-1 list-disc pl-5">
            {checks.map((check) => (
              <li key={check.layer} data-check={check.layer}>
                {check.layer}: {check.outcome}
              </li>
            ))}
          </ul>
        </section>
      )}
      {tables.length > 0 && (
        <section>
          <h3 className="text-[var(--muted)]">Where the data came from</h3>
          <ul className="mt-1 list-disc pl-5">
            {tables.map((table) => (
              <li key={table} className="font-mono">
                {table}
              </li>
            ))}
          </ul>
        </section>
      )}
      {models.length > 0 && (
        <section>
          <h3 className="text-[var(--muted)]">Model</h3>
          <p>{models.join(", ")}</p>
        </section>
      )}
      {seconds !== null && (
        <section>
          <h3 className="text-[var(--muted)]">Time</h3>
          <p>{seconds} seconds</p>
        </section>
      )}
    </div>
  );
}
