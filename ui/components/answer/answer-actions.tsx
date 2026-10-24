"use client";

import Link from "next/link";
import { useState } from "react";

import {
  answerAsPlainText,
  attemptsOfRecord,
  generatedSql,
  latencySeconds,
  layerChecks,
  modelsUsed,
  retrievedTables,
  triedFirst,
  type ResultTable,
} from "@/lib/answer-parts";
import { layerHrefForTable } from "@/lib/layer";
import { formatSql } from "@/lib/sql-format";

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

function SqlText({ sql, final }: { sql: string; final?: boolean }) {
  return (
    <pre
      data-final-sql={final ? "" : undefined}
      className="mt-1 max-h-64 overflow-auto font-mono text-xs whitespace-pre"
    >
      {formatSql(sql)}
    </pre>
  );
}

function Details({ record }: { record: Record<string, unknown> }) {
  const sql = generatedSql(record);
  const tried = triedFirst(record);
  const checks = layerChecks(attemptsOfRecord(record));
  const tables = retrievedTables(record);
  const layerHref = layerHrefForTable(tables[0]);
  const models = modelsUsed(record);
  const seconds = latencySeconds(record);
  return (
    <div data-details className="mt-3 space-y-3">
      {(sql || tried.length > 0) && (
        <section data-sql>
          <h3 className="text-[var(--muted)]">SQL</h3>
          {tried.length > 0 && (
            <div className="mt-1">
              <p>{sql ? "Tried first:" : "Tried:"}</p>
              <ol className="mt-1 space-y-2">
                {tried.map((attempt, index) => (
                  <li key={index} data-tried>
                    {attempt.sql && <SqlText sql={attempt.sql} />}
                    <p className="text-[var(--muted)]">{attempt.sentence}</p>
                  </li>
                ))}
              </ol>
            </div>
          )}
          {sql && <SqlText sql={sql} final />}
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
          {layerHref && (
            <Link href={layerHref} className="mt-1 inline-block underline">
              Open in the semantic layer
            </Link>
          )}
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
