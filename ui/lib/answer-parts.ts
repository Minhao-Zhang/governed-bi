/**
 * Readers for the pieces of an answer the page shows. Each one returns nothing when the
 * record does not have that piece, so the page omits the section.
 */

import type { z } from "zod";

import { resultTableSchema } from "./schemas.ts";

export type ResultTable = z.infer<typeof resultTableSchema>;

const CHECK_LAYERS = [
  ["PARSE", "parse"],
  ["NO_WRITE", "no write"],
  ["FUNCTIONS", "functions"],
  ["BINDING", "binding"],
  ["COLUMNS", "columns"],
  ["TABLES", "tables"],
] as const;

export type AssumptionBasis = "layer" | "convention" | "guess" | "found_in_query";

export interface Assumption {
  statement: string;
  about: string | null;
  basis: AssumptionBasis;
  asset_id: string | null;
}

export interface LayerCheck {
  layer: string;
  outcome: "passed" | "refused" | "not reached";
}

const BASES = new Set<AssumptionBasis>(["layer", "convention", "guess", "found_in_query"]);

export function shownTable(value: unknown): ResultTable | null {
  const parsed = resultTableSchema.safeParse(value);
  if (!parsed.success) return null;
  if (parsed.data.columns.length === 0 && parsed.data.rows.length === 0) return null;
  return parsed.data;
}

/** Present only when the stored total is larger than the rows on the page. */
export function rowCaption(table: ResultTable): string | null {
  const total = table.row_count;
  if (typeof total !== "number" || !Number.isFinite(total) || total <= table.rows.length) return null;
  return `Showing ${table.rows.length.toLocaleString("en-US")} of ${total.toLocaleString("en-US")} rows`;
}

export function readAssumptions(raw: unknown): Assumption[] {
  if (!Array.isArray(raw)) return [];
  const out: Assumption[] = [];
  for (const item of raw) {
    if (item === null || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    if (typeof row.statement !== "string" || row.statement === "") continue;
    if (typeof row.basis !== "string" || !BASES.has(row.basis as AssumptionBasis)) continue;
    out.push({
      statement: row.statement,
      about: typeof row.about === "string" ? row.about : null,
      basis: row.basis as AssumptionBasis,
      asset_id: typeof row.asset_id === "string" && row.asset_id !== "" ? row.asset_id : null,
    });
  }
  return out;
}

/**
 * The record stores an asset id, not a title. The last segment, with underscores as spaces,
 * is the name a reader can scan.
 */
export function layerSource(assetId: string | null): string {
  if (!assetId) return "From your definition.";
  const last = assetId.split(/[./]/).filter(Boolean).at(-1) ?? assetId;
  const words = last.replace(/[_-]+/g, " ").trim();
  return words ? `From your definition: ${words}.` : "From your definition.";
}

/**
 * The last attempt is the one that decided the turn. Reaching a layer means every layer
 * before it passed.
 */
export function layerChecks(attempts: unknown): LayerCheck[] | null {
  if (!Array.isArray(attempts) || attempts.length === 0) return null;
  const rows = attempts.filter((item): item is Record<string, unknown> => item !== null && typeof item === "object");
  if (rows.length === 0) return null;
  const chosen = rows[rows.length - 1];
  const passed = chosen.passed === true;
  const failedAt = passed ? -1 : layerIndex(chosen.result_layer);
  return CHECK_LAYERS.map(([, label], index) => ({
    layer: label,
    outcome: outcomeAt(index, passed, failedAt),
  }));
}

function outcomeAt(index: number, passed: boolean, failedAt: number): LayerCheck["outcome"] {
  if (passed) return "passed";
  if (failedAt < 0 || index > failedAt) return "not reached";
  if (index === failedAt) return "refused";
  return "passed";
}

function layerIndex(value: unknown): number {
  if (typeof value !== "string") return -1;
  const key = value.trim().toUpperCase().replace(/[\s-]+/g, "_");
  return CHECK_LAYERS.findIndex(([id]) => id === key);
}

export function generatedSql(record: Record<string, unknown>): string | null {
  const sql = record.generated_sql;
  return typeof sql === "string" && sql.trim() !== "" ? sql : null;
}

/** Why the checks stopped a statement, by rule id (`govern/layers.py::RULES`). */
export const REASON_WORDS: Record<string, string> = {
  r_unparseable: "it could not be read as SQL.",
  r_empty_statement: "there was no statement.",
  r_multiple_statements: "it held more than one statement.",
  r_control_characters: "it held hidden control characters.",
  r_ambiguous_fold: "two names in it differ only by case.",
  r_not_a_read: "it was not a read.",
  r_write_construct: "it would change data.",
  r_select_into: "it would write its result into a table.",
  r_locking_clause: "it would lock rows.",
  r_function_not_permitted: "it used a function that is not allowed.",
  r_whole_row_argument: "it passed a whole row to a function.",
  r_unbound_reference: "it named a column that none of its tables has.",
  r_ambiguous_reference: "a column in it could come from more than one table.",
  r_star_projection: "it asked for every column with *.",
  r_natural_join: "it used a natural join.",
  r_table_function: "it read from a table function.",
  r_whole_row_reference: "it used a whole row as a value.",
  r_column_not_allowed: "a column in it is not one the layer allows.",
  r_column_excluded: "a column in it is excluded from questions.",
  r_column_suspect: "a column in it is marked unreliable.",
  r_table_not_retrieved: "the table is not one retrieval found.",
  guardrail_error: "the checks broke while reading it.",
};

export interface FailedAttempt {
  sql: string | null;
  sentence: string;
}

/**
 * The query attempts that failed before the one that answered, in order. With no answering
 * attempt, every failed one. Sampling and the attempt-cap row are not attempts at the answer.
 */
export function triedFirst(record: Record<string, unknown>): FailedAttempt[] {
  const raw = attemptsOfRecord(record);
  if (!Array.isArray(raw)) return [];
  const rows = raw.filter(
    (item): item is Record<string, unknown> =>
      item !== null &&
      typeof item === "object" &&
      (item as Record<string, unknown>).path === "agent" &&
      (item as Record<string, unknown>).reason_code !== "attempt_cap",
  );
  const failed = (row: Record<string, unknown>) => row.passed !== true || text(row.error) !== null;
  const answeredAt = rows.findLastIndex((row) => !failed(row));
  const before = answeredAt >= 0 ? rows.slice(0, answeredAt) : rows;
  return before.filter(failed).map((row) => ({
    sql: text(row.submitted_sql) ?? text(row.checked_sql) ?? text(row.executed_sql),
    sentence: failureSentence(row),
  }));
}

function failureSentence(row: Record<string, unknown>): string {
  if (row.passed === true) {
    // The driver's `ExceptionType: ` prefix names our code, not the database's complaint.
    const message = (text(row.error) ?? "").replace(/^[A-Za-z_][\w.]*:\s*/, "");
    return `The database returned: ${sentenceEnd(message || "an error")}`;
  }
  const reason = typeof row.reason_code === "string" ? REASON_WORDS[row.reason_code] : undefined;
  return `The checks stopped it: ${reason ?? "it broke one of the rules."}`;
}

function sentenceEnd(value: string): string {
  const trimmed = value.trim();
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

export function attemptsOfRecord(record: Record<string, unknown>): unknown {
  const execution = record.execution;
  if (execution === null || typeof execution !== "object" || Array.isArray(execution)) return null;
  return (execution as Record<string, unknown>).attempts;
}

export function retrievedTables(record: Record<string, unknown>): string[] {
  const raw = record.retrieved_tables;
  if (!Array.isArray(raw)) return [];
  const tables = raw.filter((item): item is string => typeof item === "string" && item !== "");
  const sql = generatedSql(record);
  // Retrieval offers more tables than the query reads; show the ones the SQL names.
  return sql ? tables.filter((table) => readsTable(sql, table)) : tables;
}

function readsTable(sql: string, table: string): boolean {
  // Schema and table together: a bare table name also matches a column of that name.
  const parts = table.split(".").map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const qualified = parts.map((part) => `"?${part}"?`).join("\\.");
  return new RegExp(`(^|[^\\w"])${qualified}([^\\w"]|$)`, "i").test(sql);
}

export function modelsUsed(record: Record<string, unknown>): string[] {
  const usage = record.usage;
  if (!Array.isArray(usage)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const row of usage) {
    if (row === null || typeof row !== "object") continue;
    const model = (row as Record<string, unknown>).model;
    if (typeof model !== "string" || model === "" || seen.has(model)) continue;
    seen.add(model);
    out.push(model);
  }
  return out;
}

export function latencySeconds(record: Record<string, unknown>): number | null {
  const value = record.latency_sec;
  return typeof value === "number" && Number.isFinite(value) ? Math.round(value * 10) / 10 : null;
}

export function answerAsPlainText(text: string | null, table: ResultTable | null): string {
  const blocks: string[] = [];
  if (text && text.trim() !== "") blocks.push(text);
  if (table) {
    const lines: string[] = [];
    if (table.columns.length > 0) lines.push(table.columns.join("\t"));
    for (const row of table.rows) {
      lines.push(table.columns.map((_, index) => plainCell(row[index])).join("\t"));
    }
    if (lines.length > 0) blocks.push(lines.join("\n"));
  }
  return blocks.join("\n\n");
}

function plainCell(value: unknown): string {
  if (value == null) return "";
  return String(value);
}
