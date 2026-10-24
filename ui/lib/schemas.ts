/**
 * Zod schemas for the engine responses this UI parses: the fail-loud boundary. TypeScript types
 * are inferred from them in `lib/types.ts`.
 */

import { z } from "zod";

/* ── /capabilities ───────────────────────────────────────────────────────── */

/** One chat surface's resolved identity. Every field nullable: the engine reports what it
 *  resolved, and an offline profile with no model wired resolves none of it. */
const modelSurfaceSchema = z.object({
  id: z.string().nullable(),
  provider: z.string().nullable(),
  effort: z.string().nullable(),
});

/** The embedding surface. `dimensions` is the served width, probed rather than declared. */
const embeddingSurfaceSchema = z.object({
  id: z.string().nullable(),
  provider: z.string().nullable(),
  dimensions: z.number().nullable(),
});

export const capabilitiesSchema = z.object({
  // Free strings: `environment` is always "local" and `dialect` is read off the connector.
  environment: z.string(),
  dialect: z.string(),
  // Constant `false` / `"none"` on a live engine (ADR 0007 §7); parsed because they arrive.
  can_edit: z.boolean(),
  edit_mode: z.string().nullable(), // "none" on a live engine (backend types it as str | None)
  can_stream: z.boolean(), // LangGraph Server present → useStream, else <NoTransport/>
  has_live_model: z.boolean(),
  model: z.string().nullable(), // null in the offline profile (no model wired)
  // The clarification prompt does not check on this: it mounts on the arriving `interrupt()`,
  // so a stale flag cannot hide a question the graph is waiting on.
  can_clarify: z.boolean().optional().default(false),
  // `embedding.id` is provider-qualified (`bedrock:amazon.titan-embed-text-v2:0`) and may hold a
  // colon itself, so it is never split.
  models: z
    .object({
      agent: modelSurfaceSchema,
      utility: modelSurfaceSchema,
      embedding: embeddingSurfaceSchema,
    })
    .optional(),
  // Credential-free by construction: `user`/`password` are never parsed out of the DSN.
  connection: z
    .object({
      dialect: z.string(),
      host: z.string().optional(),
      port: z.string().optional(),
      database: z.string().optional(),
    })
    .optional(),
});

/* ── Answer (chat terminal state) ────────────────────────────────────────── */

/** The preview `run_query` kept: at most 20 rows, beside the record, not inside it. */
export const resultTableSchema = z.object({
  columns: z.array(z.string()),
  rows: z.array(z.array(z.unknown())),
  row_count: z.number().optional(),
  truncated: z.boolean().optional(),
  preview_rows: z.number().optional(),
});

/**
 * The engine's answer (engine ADR 0007 §3). No reliability tier, and none may be defaulted in.
 * `text` is the system's copy and `answer_text` the model's; never fall back from one to the
 * other. `record` stays open because the engine's `RECORD_REGISTER` is the authority.
 */
export const answerViewSchema = z.object({
  // `parseAnswer` drops an answer whose outcome is not listed, so a missing member renders no card.
  outcome: z.enum(["answered", "refused", "clarification", "capped", "crashed", "no_sql"]),
  text: z.string().nullable(),
  answer_text: z.string().nullable().optional(),
  failed_stage: z.string().nullable().optional(),
  error_type: z.string().nullable().optional(),
  refused_by: z.string().nullable().optional(),
  record: z.record(z.string(), z.unknown()).default({}),
  // Null when that turn ran no query.
  result_table: resultTableSchema.nullable().optional(),
  // Whether this turn reached the durable turn log, and why not.
  audit_logged: z.boolean().optional(),
  audit_error: z.string().nullable().optional(),
});

/* ── Clarifying question ─────────────────────────────────────────────────── */

/** Outlined answers on a clarifying question. Omitted on the wire when the question is freeform. */
export const clarificationChoicesSchema = z.array(z.string());

/* ── /conversations ──────────────────────────────────────────────────────── */

/** How a conversation ended (`api/thread_turns.py::ENDINGS`). */
export const endingSchema = z.enum(["answered", "not_answered", "waiting", "stopped", "failed"]);

export const conversationTurnSchema = z.object({
  asked_at: z.string().nullable(),
  question: z.string().nullable(),
  outcome: z.string().nullable(),
});

export const conversationRowSchema = z.object({
  thread_id: z.string().nullable(),
  updated_at: z.string().nullable(),
  status: z.string().nullable(),
  waiting: z.boolean(),
  ending: endingSchema.nullable(),
  question: z.string().nullable(),
  elided_turns: z.number(),
  turns: z.array(conversationTurnSchema),
});

export const conversationsSchema = z.object({
  rows: z.array(conversationRowSchema),
  meta: z.object({
    n: z.number(),
    total: z.number(),
    truncated: z.boolean(),
    threads_scanned: z.number(),
    limit: z.number(),
    offset: z.number(),
    ending: endingSchema.nullable(),
  }),
});

/* ── /examples ───────────────────────────────────────────────────────────── */

/** One few-shot question. The SQL stays off the wire (`docs/openapi.json`). */
export const exampleQuestionSchema = z.object({
  question: z.string(),
  db_id: z.string(),
});

export const examplesResponseSchema = z.object({
  examples: z.array(exampleQuestionSchema),
});

/** `GET /layer`: the schemas whose Ossie model the viewer can open. */
export const layerSchema = z.object({
  schemas: z.array(z.string()),
});
