/**
 * Hand-copy of the engine's `refused_by` sentences. The page shows `answer.text` for a refusal.
 * The map stays because the vocabulary test reads it and the client cannot import the registry.
 */

import type { AnswerView } from "@/lib/types";

export type AnswerDelivery = "clean" | "graded" | "refused" | "no_statement";

/** `no_sql` is its own state. `graded` is a ledger terminal nothing writes; the function still names it. */
export function deriveDelivery(answer: AnswerView): AnswerDelivery {
  if (answer.outcome === "no_sql") return "no_statement";
  if (answer.outcome !== "answered") return "refused";
  if (terminalOf(answer) === "graded") return "graded";
  return "clean";
}

/** The engine's `execution.terminal`, or null when nothing recorded one. */
function terminalOf(answer: AnswerView): string | null {
  const execution = answer.record?.execution;
  if (execution && typeof execution === "object" && !Array.isArray(execution)) {
    const terminal = (execution as Record<string, unknown>).terminal;
    return typeof terminal === "string" ? terminal : null;
  }
  return null;
}

/**
 * The text to show. `answer_text` is the model's answer and `text` the system's refusal copy
 * (engine ADR 0007 §4); this picks by outcome rather than falling back from one to the other,
 * which would render system copy as though the model had said it.
 */
export function displayText(answer: AnswerView): string | null {
  if (answer.outcome === "answered" || answer.outcome === "no_sql") {
    return answer.answer_text ?? null;
  }
  return answer.text ?? answer.answer_text ?? null;
}

/**
 * The ledger's attempts. An empty list means no statement was attempted, which differs from
 * attempts that all failed, so the caller gets the array rather than a count.
 */
export function attemptsOf(answer: AnswerView): Array<Record<string, unknown>> {
  const execution = answer.record?.execution;
  if (execution && typeof execution === "object" && !Array.isArray(execution)) {
    const attempts = (execution as Record<string, unknown>).attempts;
    if (Array.isArray(attempts)) return attempts as Array<Record<string, unknown>>;
  }
  return [];
}

/** The turn record as an open key/value map. */
export function provenanceOf(answer: AnswerView): Record<string, unknown> {
  return answer.record ?? {};
}

/**
 * One reader-facing sentence per `refused_by` value: a hand-copy of the engine's closed
 * vocabulary, `register/stages.py::REFUSED_BY_TO_STAGE`, which this client cannot import.
 * `tests/api/test_the_refusal_phrasing_covers_the_vocabulary.py` checks both directions.
 */
const REFUSED_BY_SENTENCE: Record<string, string> = {
  // Stage.route — nothing in the corpus scored against the question at all.
  no_schema_matched:
    "Nothing in your data looked related to this question, so no query was written.",
  // Stage.abstain — the four declared abstentions (ADR 0013).
  nothing_retrieved:
    "We found nothing in your data that could answer this, so nothing was queried.",
  empty_context:
    "We had no description of your data to work from, so any answer would have been guessed.",
  retrieved_table_dropped:
    "The right table was found but did not fit in this turn, so the question was not answered from it.",
  retrieval_channel_failed:
    "One of the steps that finds relevant data failed, so this turn was not answered rather than answered from the wrong tables.",
  // Stage.connect — the shape of the question needs relationships that are not declared.
  missing_join_path:
    "Answering this needs two tables linked together, and no relationship between them is declared.",
  over_connect_bounds:
    "Answering this would need more tables joined than this engine will join at once.",
  // Also Stage.connect, and not phrased as a join problem: nothing was joined, the reference
  // closure around the matched tables reached further than the engine follows.
  over_resolve_bounds:
    "The tables that matched this question are linked to more of the semantic layer than this engine will pull in at once.",
  // Stage.guard — refused before any retrieval.
  guard: "The question was stopped by an input check before anything was queried.",
  // Stage.check — the layer stack refused the statement itself.
  guardrail:
    "A query was written and the safety checks refused it, so it never ran against your data.",
  // Stage.cap — attempts exhausted.
  attempt_cap:
    "Several queries were written and none passed the safety checks, so the attempts ran out.",
  // Our own faults. Named as ours: `CRASH_REFUSED_BY` classifies these as `crashed`, not as a
  // decision the product took, and a reader should not go looking at their corpus for our bug.
  model_error: "Something went wrong on our side while writing the query.",
  guardrail_error: "Something went wrong on our side while checking the query.",
};

/**
 * How this turn ended, in one sentence, or `null` when the delivery speaks for itself. Every
 * input is a field the engine produced. An unrecognised `refused_by` shows raw rather than
 * hidden, which is the pressure to add the missing key.
 */
export function refusalSentence(answer: AnswerView): string | null {
  const delivery = deriveDelivery(answer);
  const refusedBy = answer.refused_by ?? null;

  if (refusedBy) {
    return (
      REFUSED_BY_SENTENCE[refusedBy] ??
      `This turn produced no answer (${refusedBy}).`
    );
  }
  if (delivery === "no_statement") {
    return "This was answered without running any query against your data.";
  }
  if (delivery === "graded") {
    return "A query ran, but not every attempt passed the safety checks.";
  }
  return null;
}
