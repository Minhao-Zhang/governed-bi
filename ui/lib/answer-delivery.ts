/**
 * Client-side delivery state for an AnswerView.
 *
 * The engine has no first-class `delivery` field, so the three render states are derived
 * from what it does emit: `outcome` and the attempt ledger's terminal (see
 * `deriveDelivery`). Branch UI on `deriveDelivery`, never on a raw field — that keeps the
 * mapping in one place the day the ledger grows a state.
 */

import type { AnswerView } from "@/lib/types";

export type AnswerDelivery = "clean" | "graded" | "refused" | "no_statement";

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
 *
 * `"no_statement"` is the fourth state and it exists because three collapsed two different
 * turns. `outcome: "no_sql"` means the turn ended and executed no governed statement — nothing
 * refused it and nothing failed — so under `outcome !== "answered"` it rendered the red refusal
 * panel with the system's "This question can't be answered as asked" copy over the top of the
 * model's own words. That is the same defect the engine side of this change fixes, one layer out:
 * the interface asserting a decision the record does not carry. It is its own state rather than
 * folded into `"clean"`, because a turn that queried nothing is also not a clean delivery.
 */
export function deriveDelivery(answer: AnswerView): AnswerDelivery {
  if (answer.outcome === "no_sql") return "no_statement";
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
 *
 * `no_sql` reads the model's field for the same reason `answered` does: nothing wrote system
 * copy for that turn (`stamp` sets `text` only on refuse and decline), so the prose is all the
 * turn produced and the `??` chain would have reached it anyway. Named explicitly so it is a
 * decision rather than a coincidence of the fallback order.
 */
export function displayText(answer: AnswerView): string | null {
  if (answer.outcome === "answered" || answer.outcome === "no_sql") {
    return answer.answer_text ?? null;
  }
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

/**
 * Business-mode phrasing for the ledger's `terminal` token.
 *
 * Ported from the downstream fork (`utkuai/detentai-fork`, its item I-3). The plan document it
 * cites, `detent-ai-trust-loop-plan.md`, is not in this repository; the finding is kept and
 * attributed to the fork rather than dropped for want of a citation we cannot check.
 *
 * `terminal` reaches a reader here at `engineer` only, as the raw `ledger: <token>` badge
 * (`reliability-stamp.tsx`). That is the right treatment for someone who reads ledgers and the
 * wrong one for everyone else: at `business` there was no translation at all, so a reader could
 * not tell an answer that queried their database from one recited out of a corpus definition —
 * the very distinction `lib/display-mode.ts` says `business` mode is for ("the answer and
 * whether it consulted your data").
 *
 * **Preventive. No turn in this tree has shown the defect.** Twelve answered turns measured
 * against the live corpus on 2026-08-19/20 each recorded exactly one passed attempt
 * (`attempts=1`, `reason_code=passed`): zero recitation, so nothing observed is being fixed here
 * and this comment does not claim otherwise. What makes it worth taking now is the corpus this
 * deployment actually serves, whose prose carries hard figures of its own (`177,714`,
 * `$83,521,791`, `613,685`). A turn that answered out of a definition would therefore put a
 * real-looking number on screen with nothing under it, which makes recitation-without-query a
 * live risk rather than a hypothetical.
 *
 * `no_sql` is deliberately **not** one entry here — see `NO_SQL_LABEL`.
 *
 * An **unrecognised** terminal falls through to the raw token rather than vanishing, on the
 * principle `reliability-stamp.tsx` already states for an outcome it does not recognise: an
 * unfamiliar state should be visible, not styled away. It reads worse than the mapped phrases,
 * which is the intended pressure to add the missing key — the same trade `refusalSentence`
 * makes.
 */
const TERMINAL_LABEL: Record<string, string> = {
  answered: "ran a query against your data",
  // Unreachable today, kept for the reason `deriveDelivery` keeps its `"graded"` branch: the
  // terminal is declared in `govern/ledger.py::ExecutionRecord` and `serve/ledger.py`'s
  // derivation never writes it.
  graded: "ran a query, and the result was checked and flagged",
  capped: "stopped after the attempt limit",
  // The fork says "refused, and the rule is named". Ours does not name it: the `refused by
  // <token>` badge is `engineer`-only in `reliability-stamp.tsx`, so at `business` that phrase
  // would promise a name the reader never sees. `refusalSentence` carries the *reason* in every
  // mode, which is the part they can act on.
  refused: "refused before any answer was produced",
  // Not in the fork's map, and its absence is a hole: `crashed` is in the engine's declared
  // vocabulary (`govern/ledger.py::ExecutionRecord.terminal` — six tokens, the fork translates
  // four), so a crashed turn fell through to the raw word. Phrased as ours, the way
  // `REFUSED_BY_SENTENCE` phrases `model_error`/`guardrail_error`: a reader should not go
  // looking at their own data for our bug.
  crashed: "stopped because something broke on our side",
};

/**
 * The two `no_sql` phrasings, split on whether the attempt ledger is empty.
 *
 * `serve/ledger.py::execution_from_attempts` returns `no_sql` whenever there are no *answering*
 * attempts, and says so explicitly: "a turn that sampled a column and then answered from context
 * is `no_sql` with a non-empty ledger". So one token covers two materially different turns. A
 * non-empty ledger means the agent touched the data first and then answered from a definition; an
 * empty one means it never touched the data at all, which is the stronger claim and gets the
 * stronger wording.
 *
 * The fork shipped a single phrase for both and split it after a live run on **2026-08-16** found
 * the second case getting the first case's softer phrasing.
 */
const NO_SQL_LABEL = {
  sampled: "answered from a definition, without running a query",
  untouched: "answered without consulting your data at all",
};

/**
 * `terminal`, in business-mode language, as a verb phrase to follow "This turn".
 *
 * Call this only where the mode check has already decided to translate — `analyst` and
 * `engineer` keep the raw token, which they can read and which is more precise (see
 * `answer-card.tsx`). Lives beside `terminalOf` and `attemptsOf`, the two readers it translates,
 * so a caller that read the raw token and built its own phrase would be a second copy of this
 * rule — the failure `answer-card.tsx`'s own docstring names for `deriveDelivery`.
 *
 * `attempts` is the same ledger `ReliabilityStamp` counts and is consulted **only** to split
 * `no_sql`, so a caller translating any other terminal may omit it.
 */
export function terminalLabel(
  terminal: string | null,
  attempts: Array<Record<string, unknown>> = [],
): string | null {
  if (terminal === null) return null;
  if (terminal === "no_sql") {
    return attempts.length > 0 ? NO_SQL_LABEL.sampled : NO_SQL_LABEL.untouched;
  }
  return TERMINAL_LABEL[terminal] ?? terminal;
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

/**
 * One plain-language sentence per `refused_by` value — what happened, in terms of the thing a
 * reader could act on.
 *
 * **A hand-copy of the engine's closed vocabulary**, `register/stages.py::REFUSED_BY_TO_STAGE`.
 * This client cannot import it (ADR 0007: it shares the repository and nothing else), so
 * `tests/api/test_the_refusal_phrasing_covers_the_vocabulary.py` checks both directions — the same
 * arrangement `ui/lib/provenance.ts` has with the record register, and for the same reason: a
 * hand-copy of a list that grows is a silent degradation. There, 32 copied keys had stopped
 * existing and 35 register fields were never listed, and nothing failed because nothing checked.
 *
 * The sentences are deliberately not the engine's own `why` text. `serve/nodes/abstain.py` gives
 * each abstention a precise reason written for whoever maintains the pipeline — it names Layer 6,
 * the character budget, the shortlist. Those are the right words for an engineer and the wrong ones
 * for the reader this mode exists for, so the engineer keeps the record and the reader gets this.
 *
 * Nothing here softens a refusal into an answer. Each sentence says the turn produced nothing and
 * why; that is the product working, and the interface should not apologise for it.
 */
const REFUSED_BY_SENTENCE: Record<string, string> = {
  // Stage.route — nothing in the corpus scored against the question at all.
  no_schema_matched:
    "Nothing in your data looked related to this question, so no query was written.",
  // Stage.abstain — the four declared abstentions (ADR 0013).
  nothing_licensed:
    "We found nothing in your data that could answer this, so nothing was queried.",
  empty_context:
    "We had no description of your data to work from, so any answer would have been guessed.",
  licensed_table_evicted:
    "The right table was found but did not fit in this turn, so the question was not answered from it.",
  retrieval_channel_failed:
    "One of the steps that finds relevant data failed, so this turn was not answered rather than answered from the wrong tables.",
  // Stage.connect — the shape of the question needs relationships that are not declared.
  missing_join_path:
    "Answering this needs two tables linked together, and no relationship between them is declared.",
  over_connect_bounds:
    "Answering this would need more tables joined than this engine will join at once.",
  // Stage.guard / Stage.negative_gate — refused before any retrieval.
  guard: "The question was stopped by an input check before anything was queried.",
  negative_example:
    "This question matches one the corpus marks as one not to answer from this data.",
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
 * What to tell a reader about how this turn ended, or `null` when the delivery speaks for itself.
 *
 * Derived from `deriveDelivery` plus `refused_by`, and from nothing else — every input is a field
 * the engine produced. That is ADR 0007 §3's rule, which forbade v1's reliability tier on the
 * answer card: render what was observed, never synthesise a judgement. A sentence here is a
 * translation of a recorded value, and when there is no recorded value there is no sentence.
 *
 * The unrecognised-value fallback shows the raw string rather than hiding it, on the same principle
 * `reliability-stamp.tsx` states: an unfamiliar state should be visible, not styled away. It reads
 * worse than the mapped sentences, which is the intended pressure to add the missing key.
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
    // `Outcome.no_sql` with nothing refusing it: the turn answered from a stored definition
    // rather than from the database. Honest about it, because the prose above will read like a
    // current fact and it is not one.
    return "This was answered without running any query against your data.";
  }
  if (delivery === "graded") {
    return "A query ran, but not every attempt passed the safety checks.";
  }
  return null;
}

/** Every `refused_by` this build has a sentence for. Read by the conformance test. */
export const PHRASED_REFUSALS: readonly string[] = Object.keys(REFUSED_BY_SENTENCE);

/**
 * The `refused_by` values a refusal should answer with what the corpus *can* see.
 *
 * Ported from the downstream fork (`utkuai/detentai-fork`, its item I-5), which scoped it to
 * `no_schema_matched` alone. Ours is wider, and the rule that widened it is: **a glimpse belongs
 * on a refusal whose meaning is a claim about coverage.** For those, naming a few of the tables
 * that do exist is either orientation — the claim was right, and the reader now sees where the
 * boundary is — or a visible contradiction, which is the more valuable outcome, because a
 * refusal contradicted on screen is a false refusal the reader can report. Every other reason in
 * `REFUSED_BY_SENTENCE` is a claim about something else, and a table list under it would be
 * noise or worse:
 *
 * - `no_schema_matched` — the central case, and the fork's only one. Fires at `Stage.route`
 *   before the agent runs, so there is no ledger, no SQL and nothing else on the record to show.
 * - `nothing_licensed` — "we found nothing in your data that could answer this" is a coverage
 *   claim by construction.
 * - `empty_context` — "we had no description of your data to work from" is the strongest
 *   coverage claim of the three, and `/schema/summary` is an independent route: if it returns
 *   tables, the refusal is falsified in front of the reader.
 * - `guard` — **the measured case, and the reason this port is worth taking.** On 2026-08-19 two
 *   questions taken from a real client SOW were refused at `Stage.guard` in 6.6–6.7 s on ~191
 *   tokens, and one of them was documented-answerable: the table it needed exists and holds
 *   67,040 rows. `govern/guard.py::GUARD_PUBLIC_MESSAGE` is deliberately one fixed string, so
 *   both refusals read identically and finding the false one took a probe harness. A refusal that
 *   volunteers its coverage would have put the contradiction on screen. It does not weaken that
 *   one-string design: the glimpse is the same catalog on every turn, so no *rule* becomes
 *   inferable from it.
 *
 * Excluded, and why: `missing_join_path` / `over_connect_bounds` / `licensed_table_evicted` all
 * *found* the tables — listing them says nothing the sentence has not already said better.
 * `guardrail` / `attempt_cap` refused a written statement, so coverage is not the question.
 * `negative_example` is a curator's deliberate "do not answer this from this data", and
 * answering it with a catalog would undercut a decision someone made on purpose.
 * `retrieval_channel_failed`, `model_error` and `guardrail_error` are our own faults, and their
 * sentences say so; a table list would point the reader back at their corpus.
 *
 * Every member must also be a key of `REFUSED_BY_SENTENCE` — the glimpse is appended to that
 * sentence, so a member with no sentence would render a bare catalog under the unrecognised-value
 * fallback. `scripts/check-answer-delivery.ts` pins that.
 */
export const CATALOG_GLIMPSE_REFUSALS: readonly string[] = [
  "no_schema_matched",
  "nothing_licensed",
  "empty_context",
  "guard",
];

/**
 * Should this refusal fetch and show the catalog glimpse?
 *
 * A predicate rather than an inline `includes`, because the answer gates a **network request**
 * (`useSchemaSummary`'s `enabled`) as well as a render, and those two must not be able to
 * disagree: enabled-but-not-rendered is a fetch nobody reads, rendered-but-not-enabled is a
 * permanently empty glimpse.
 *
 * Deliberately **not** also gated on the engine having supplied no `text`, which is how the fork
 * scopes it. `serve/nodes/guard.py` blocks with `GUARD_PUBLIC_MESSAGE`, so a guard refusal always
 * carries `text` — and that generic copy is exactly what hid the false refusal above. Gating on
 * `text === null` would exclude the one measured case this port exists for.
 */
export function wantsCatalogGlimpse(refusedBy: string | null): boolean {
  return refusedBy !== null && CATALOG_GLIMPSE_REFUSALS.includes(refusedBy);
}

/**
 * "What we can see" line for a refusal that found nothing to match.
 *
 * Deliberately short. `tableNames` is expected pre-filtered by the caller — `governance.excluded`
 * tables never reach the model's context (`serve/session.py::_visible`), so naming one would
 * promise coverage that does not exist — and this still caps the count itself, so a large corpus
 * renders a glimpse rather than a dump.
 *
 * Two phrasings, where the fork has one ("What I can see: …"). `/schema/summary` returns a
 * **page**: `schemaSummaryResponseSchema` carries `total` separately from `items`, and the caller
 * only ever holds the first page. "What I can see: a, b, c" reads as the whole catalog, which on
 * the 57-schema corpus this tree serves it is not. First person is dropped for the same reason
 * `REFUSED_BY_SENTENCE` says "we": the engine is not a character.
 */
export function catalogGlimpse(tableNames: string[], limit = 5): string | null {
  if (tableNames.length === 0) return null;
  const shown = tableNames.slice(0, limit).join(", ");
  return tableNames.length > limit
    ? `Some of the tables we can see: ${shown}.`
    : `The tables we can see: ${shown}.`;
}
