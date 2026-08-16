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

/**
 * Business-tier phrasing for the ledger's `terminal` token (utku-ai-trust-loop-plan.md, I-3).
 *
 * `serve/ledger.py` is explicit about what `no_sql` means -- "a turn that sampled a column
 * and then answered from context is `no_sql` with a non-empty ledger" -- but that is ledger
 * vocabulary, not customer vocabulary: a restaurant owner reads `no_sql` and learns nothing
 * about whether the number in front of them came from their database.
 *
 * `no_sql` is deliberately **not** a single entry here. `execution_from_attempts` returns it
 * whenever there are no *answering* attempts, which covers two different turns: "sampled a
 * column, then answered from context" (a non-empty ledger) and "never touched the data at
 * all" (an empty ledger). I-3 originally shipped one phrase for both; the live run on
 * 2026-08-16 found the second case is a stronger claim getting the same, softer phrasing.
 * `terminalLabel` branches it on `attempts.length` instead -- see `NO_SQL_LABEL`.
 *
 * Lives here, beside `terminalOf`, so a caller reading the raw token and building its own
 * phrase is a second copy of this rule -- the same failure `answer-card.tsx`'s own docstring
 * names for `deriveDelivery`. An **unrecognised** terminal falls through to the raw value,
 * the same principle `reliability-stamp.tsx` already states for an outcome it does not
 * recognise: an unfamiliar state should be visible, not invisible.
 */
const TERMINAL_LABEL: Record<string, string> = {
  answered: "ran a query against your data",
  graded: "ran a query; the result was checked and flagged",
  capped: "stopped after the attempt limit",
  refused: "refused, and the rule is named",
};

/** The two `no_sql` phrasings, split on whether the attempt ledger is empty. A non-empty
 * ledger means the agent tried the data first (e.g. sampled a column) and then answered from
 * a definition; an empty one means it never touched the data at all, which is the stronger
 * claim and gets the stronger warning. */
const NO_SQL_LABEL = {
  sampled: "answered from a definition, without running a query",
  untouched: "answered without consulting your data at all",
};

/** `terminal`, in business-tier language. Analyst/engineer keep the raw token -- call this
 * only where the tier check already decided to translate (see `answer-card.tsx`). `attempts`
 * is the same ledger `ReliabilityStamp` counts; it is only consulted to split `no_sql` (see
 * `NO_SQL_LABEL`), so a caller translating a different terminal may omit it. */
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

/**
 * Plain-language sentence for `refused_by` (utku-ai-trust-loop-plan.md, I-5).
 *
 * `refused_by` is the engine's closed vocabulary for *why* it withheld an answer
 * (`register/stages.py::REFUSED_BY_TO_STAGE`, plus the abstention reasons in
 * `ABSTENTION_REASONS`), hand-mirrored here because Python is not importable from
 * TypeScript -- so this map and that inventory are two declarations of one vocabulary and can
 * drift. Every sentence is written for the person who asked the question, about *their* data
 * and *their* question, not the engine's stages or layers: "I couldn't find anything about
 * that in your data," never "the router returned an empty shortlist."
 *
 * `guardrail_error` and `model_error` read differently on purpose. `stages.py`'s own comment
 * calls `guardrail_error` "our bug wearing a refusal stamp" -- the 2026-08-10 audit found a
 * turn whose every attempt died inside `check()` recorded `outcome: refused` when `Outcome`
 * requires a crash to stay separate from a refusal (`CRASH_REFUSED_BY` holds both). Both say
 * plainly that something broke, so a reader can tell "I can't answer that" apart from
 * "something broke here." Every other reason is the product declining on purpose, so it says
 * so without apology or hedging -- `docs/failure-modes.md` prices declining as an outcome, not
 * a failure.
 *
 * An **unrecognised** `refused_by` falls through to the raw token, the same principle
 * `reliability-stamp.tsx` already states: an unfamiliar state should be visible, not
 * invisible.
 */
const REFUSAL_REASON_LABEL: Record<string, string> = {
  guard: "I'm not able to answer that kind of question.",
  negative_example: "I've been specifically told not to answer this kind of question.",
  no_schema_matched: "I couldn't find anything about that in your data.",
  missing_join_path:
    "I can see pieces of what you're asking about, but I don't know how they connect in your data.",
  over_connect_bounds:
    "Answering that would mean connecting more of your data at once than I'm allowed to.",
  guardrail:
    "I couldn't put together a query for that which passes the safety checks on your data.",
  guardrail_error:
    "Something broke on my end while checking that question — this isn't a normal refusal. Please try again or let your admin know.",
  attempt_cap:
    "I tried several times to answer this from your data, and none of the attempts passed my checks.",
  model_error:
    "Something went wrong while I was generating an answer — this isn't a normal refusal. Please try again or let your admin know.",
  retrieval_channel_failed:
    "I wasn't able to search all of your data just then, so I'm not going to guess at an answer.",
  nothing_licensed: "None of your data is set up to answer that kind of question yet.",
  empty_context: "I didn't have anything from your data to work with for that question.",
  licensed_table_evicted:
    "The table I'd need was too large to include for this question, so I couldn't answer it.",
};

/** `refused_by`, as a sentence a non-technical reader can act on. Falls through to the raw
 * token for a reason this map does not recognise -- the same fall-through `terminalLabel` and
 * `reliability-stamp.tsx` use. `null` (no refusal reason recorded) returns null, same as
 * `terminalLabel`. */
export function refusalSentence(refusedBy: string | null): string | null {
  if (refusedBy === null) return null;
  return REFUSAL_REASON_LABEL[refusedBy] ?? refusedBy;
}

/**
 * Business-tier phrasing for `outcome` (utku-ai-trust-loop-plan.md, task A-0).
 *
 * `terminal` and `refused_by` got their own plain-language treatment in I-3/I-5; `outcome` did
 * not, even though it is the *first* badge on the card (`reliability-stamp.tsx` renders it
 * before either of the others). So a business reader whose turn hit `guardrail_error` read
 * `crashed` -- a raw ledger token -- before ever reaching the sentence that explains it.
 *
 * `answered` and `refused` map to themselves: both are already plain English, and inventing a
 * different phrase where the raw token already is the plain one is the thing `terminalLabel`
 * and `refusalSentence` both avoid too. `crashed` reads the same as `REFUSAL_REASON_LABEL`'s own
 * `guardrail_error`/`model_error` entries -- both name a bug, not a decision, on purpose (see
 * that map's own comment on why those two "read differently on purpose").
 */
const OUTCOME_LABEL: Record<string, string> = {
  answered: "answered",
  refused: "refused",
  clarification: "waiting on a clarification",
  capped: "stopped after too many tries",
  crashed: "something broke on our end",
};

/** `outcome`, in business-tier language. Analyst/engineer keep the raw token -- call this only
 * where the tier check already decided to translate (see `reliability-stamp.tsx`). Same
 * fall-through as `terminalLabel`/`refusalSentence`: an outcome this map does not recognise is
 * shown raw, not hidden. */
export function outcomeLabel(outcome: string): string {
  return OUTCOME_LABEL[outcome] ?? outcome;
}

/**
 * "What I can see" line for a `no_schema_matched` refusal (I-5). `no_schema_matched` fires at
 * `Stage.route`, before the agent ever ran -- there is no attempt ledger, no SQL, nothing else
 * to show. Naming a handful of the tables the engine actually has turns that dead end into
 * orientation instead of a blank. Deliberately short: `tableNames` is expected pre-filtered
 * (e.g. excluded tables dropped) by the caller, and this still caps the count itself so a
 * large corpus renders a glimpse, not a dump.
 */
export function catalogGlimpse(tableNames: string[], limit = 5): string | null {
  if (tableNames.length === 0) return null;
  return `What I can see: ${tableNames.slice(0, limit).join(", ")}.`;
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

/** Quiet "schemas considered" line from `record.schemas` — the routed top-N, which is v2's
 * `register/record.py` analog of v1's `provenance.routed_schemas`.
 *
 * Reads the record, not a provenance bag. The v1 spelling was still here and its own comment
 * conceded it "returns null on a live turn", which is what reading a field the register does
 * not declare looks like from the inside. */
export function routedSchemasLabel(record: Record<string, unknown>): string | null {
  const schemas = asStringArray(record.schemas);
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
