/**
 * Serve-time clarification (HITL) — the server ↔ frontend wire contract.
 *
 * When the governed agent hits genuine ambiguity mid-turn it asks ONE question
 * and waits, instead of guessing or refusing. The interrupt payload and the id/why fields
 * are ADR 0007 §6; the plan that first specified this is
 * `git-history:docs/plans/hitl-clarification-contract.md`.
 * This rides the SAME `useStream` connection as the answer and the governance
 * event stream — no new endpoint, no new socket:
 *
 *  - Server raises `interrupt(request)` inside its `ask_user` tool; the request
 *    IS the `ClarificationRequest` below and surfaces as `stream.interrupt.value`.
 *  - Client answers by resuming the run with a `ClarificationResponse`
 *    (`stream.submit(null, { command: { resume } })` on the shipped SDK).
 *
 * The interrupt value arrives typed as `unknown`, so `clarificationRequestSchema`
 * is the fail-loud boundary (mirrors `lib/schemas.ts`): a malformed interrupt is
 * dropped, never rendered half-parsed. Gated end-to-end on `capabilities.can_clarify`.
 */

import { z } from "zod";

/* ── Server → client: the `interrupt()` value (contract §3) ──────────────── */

export const clarificationChoiceSchema = z.object({
  id: z.string(),
  label: z.string(),
});

export const clarificationRequestSchema = z.object({
  kind: z.literal("clarification"), // discriminator; reserved for future interrupt kinds
  clarification_id: z.string(), // join key across interrupt, resume, timeline event, provenance
  question: z.string(),
  why: z.string(), // governance transparency: the user always sees WHY they're asked
  basis: z.enum(["data_definition", "ranking_ambiguity"]).optional(),
  // Absent ⇒ freeform-only. Present ⇒ render the options; `allow_freeform`
  // decides whether a text box is also offered alongside them.
  choices: z.array(clarificationChoiceSchema).optional(),
  allow_freeform: z.boolean().optional(),
  tier: z.string().optional(), // provenance tier of the question (D12); "audit" today
});

/* ── Client → server: the `resume` value (contract §4) ───────────────────── */

/**
 * Exactly one of `answer` / `choice_id` / `declined` / `deferred` / `cancelled` is set.
 * `declined` fails closed server-side (the agent does not guess); `deferred` proceeds
 * under the stated why.
 *
 * This client sends only the first four. `cancelled` stays in the union because the
 * wire still accepts it — `parse_resume` folds it into the same fail-closed branch as
 * `declined` — but no surface here produces it: a second button doing exactly what
 * Decline does was a choice between synonyms, so it was removed.
 */
export type ClarificationResponse =
  | { clarification_id: string; answer: string }
  | { clarification_id: string; choice_id: string }
  | { clarification_id: string; declined: true }
  | { clarification_id: string; deferred: true }
  | { clarification_id: string; cancelled: true };

export type ClarificationChoice = z.infer<typeof clarificationChoiceSchema>;
export type ClarificationRequest = z.infer<typeof clarificationRequestSchema>;

/** Parse a raw `stream.interrupt.value` into a request, or null if it isn't one. */
export function parseClarification(raw: unknown): ClarificationRequest | null {
  const parsed = clarificationRequestSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}
