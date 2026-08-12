/**
 * Client-side delivery state for an AnswerView.
 *
 * The engine has no first-class `delivery` field, so the three render states are derived
 * from what it does emit: `outcome` and the attempt ledger's terminal (see
 * `deriveDelivery`). Branch UI on `deriveDelivery`, never on a raw field — that keeps the
 * mapping in one place the day the ledger grows a state.
 */

import type { AnswerView } from "@/lib/types";

export type AnswerDelivery = "clean" | "graded" | "refused";

/**
 * One plain-language line per uncertainty flag — a flag with no entry here would fire the
 * warning treatment with nothing to explain it, which is the failure mode this map exists
 * to prevent.
 *
 * **Inert today.** No engine path writes `uncertainty_flags` into the record: the v1
 * producer (`analyst/answer.py`'s `UncertaintySignals`) did not survive the v2 rewrite and
 * nothing replaced it, so `whyLines` returns `[]` on every live turn. Kept rather than
 * deleted because the vocabulary is still the right one and the banner is already wired;
 * do not read this list as evidence that the engine flags uncertainty.
 */
const FLAG_WHY: Record<string, string> = {
  low_confidence_join: "Joined tables on a relationship we're not fully sure of.",
  suspect_in_scope: "Used a column that may be unreliable (flagged during curation).",
  repaired: "Needed multiple attempts to produce valid SQL.",
  fenced_raw_fallback: "Fell back to a raw query without the governed layer.",
  weak_retrieval: "The corpus may not cover what you asked about.",
  // Reserved on the wire: no serve path writes it today, so it never appears.
  // Carried anyway so a future Corrective-RAG loop explains itself on arrival.
  corrective_rag: "Retrieved evidence had to be corrected before answering.",
};

/**
 * Derive the delivery discriminator from a live AnswerView.
 *
 * **Retargeted to the v2 engine.** The old rule read `sql == null`,
 * `semantic_assurance` and `provenance.graded_delivery`; none of those exists now. The
 * engine's `outcome` is an observation of what happened, so it is the discriminator:
 * `answered` is a delivery, everything else is not.
 *
 * `"graded"` is kept and is currently **unreachable**, deliberately. The engine's ledger
 * vocabulary declares a `graded` terminal (`govern/ledger.py`) that nothing writes yet, so
 * the state exists on the wire and not in practice. Deleting the branch would mean
 * re-deriving it the day the grader path lands; leaving it wired means the UI already
 * handles that turn. It is mapped from the ledger, not guessed from an assurance level
 * that no longer exists.
 */
export function deriveDelivery(answer: AnswerView): AnswerDelivery {
  if (answer.outcome !== "answered") return "refused";
  if (terminalOf(answer) === "graded") return "graded";
  return "clean";
}

/** The engine's `execution.terminal`, or null when nothing recorded one. */
export function terminalOf(answer: AnswerView): string | null {
  const execution = answer.record?.execution;
  if (execution && typeof execution === "object" && !Array.isArray(execution)) {
    const terminal = (execution as Record<string, unknown>).terminal;
    return typeof terminal === "string" ? terminal : null;
  }
  return null;
}

/** The governed statement the engine actually ran, from the record. */
export function sqlOf(answer: AnswerView): string | null {
  const sql = answer.record?.generated_sql;
  return typeof sql === "string" && sql.length > 0 ? sql : null;
}

/**
 * The text to show as the answer.
 *
 * `answer_text` is the **model's** answer; `text` is the **system's** copy — a refusal or
 * decline message. They are separate fields in the engine on purpose (engine ADR 0007 §4),
 * so this picks by delivery rather than falling back from one to the other: a silent
 * fallback would render a refusal's system copy as though the model had said it.
 */
export function displayText(answer: AnswerView): string | null {
  if (answer.outcome === "answered") return answer.answer_text ?? null;
  return answer.text ?? answer.answer_text ?? null;
}

/**
 * The ledger's attempts, for the reliability stamp.
 *
 * An **empty** list is not the same as no ledger: a turn where governance refused every
 * statement has attempts that did not pass, and a turn that never produced SQL has none at
 * all. Both are honest and they are different, so this returns the array and lets the
 * caller distinguish them rather than collapsing to a count.
 */
export function attemptsOf(answer: AnswerView): Array<Record<string, unknown>> {
  const execution = answer.record?.execution;
  if (execution && typeof execution === "object" && !Array.isArray(execution)) {
    const attempts = (execution as Record<string, unknown>).attempts;
    if (Array.isArray(attempts)) return attempts as Array<Record<string, unknown>>;
  }
  return [];
}

/** The record, as the provenance drawer's open key/value map. */
export function provenanceOf(answer: AnswerView): Record<string, unknown> {
  return answer.record ?? {};
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === "string");
}

/**
 * Plain-language "why" lines from `provenance.uncertainty_flags`. When `suspect_columns`
 * is present, name the columns on the suspect flag. Both keys are absent from the v2
 * record — see `FLAG_WHY` — so this returns `[]` today.
 */
export function whyLines(provenance: Record<string, unknown>): string[] {
  const flags = asStringArray(provenance.uncertainty_flags);
  const suspects = asStringArray(provenance.suspect_columns);
  const lines: string[] = [];

  for (const flag of flags) {
    if (flag === "suspect_in_scope" && suspects.length > 0) {
      lines.push(
        `Used a column that may be unreliable (flagged during curation): ${suspects.join(", ")}.`,
      );
      continue;
    }
    const text = FLAG_WHY[flag];
    if (text) lines.push(text);
  }

  return lines;
}

/** Quiet "schemas considered" line from `routed_schemas`. Not a record register field
 * today, so this returns null on a live turn. */
export function routedSchemasLabel(provenance: Record<string, unknown>): string | null {
  const schemas = asStringArray(provenance.routed_schemas);
  if (schemas.length === 0) return null;
  return `Schemas considered: ${schemas.join(", ")}`;
}

/**
 * Which corpus release produced this answer. Production inference reads a pinned corpus,
 * never the live working copy, so the answer should name the version behind it.
 *
 * Reads `provenance.corpus_release_hash`, which the v2 record does not carry — the engine's
 * equivalent is `corpus_content_hash` on the record, and repointing this is a real change
 * rather than a rename, so it is not done here. Abbreviated to 7 chars, git-style. Returns
 * null when unset, which is every turn today — an unpinned corpus must not be made to look
 * pinned.
 */
export function corpusVersionLabel(provenance: Record<string, unknown>): string | null {
  const hash = provenance.corpus_release_hash;
  if (typeof hash !== "string" || hash.length === 0) return null;
  return `corpus @ ${hash.slice(0, 7)}`;
}
