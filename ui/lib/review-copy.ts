/**
 * Every user-facing string on the return path's surfaces, in one module.
 *
 * **Why one module.** This project's UI prose states what a surface *cannot* do — the pending
 * queue's own component says answering from it is refused and why — and that discipline survives
 * only if the strings are somewhere a person can read as a set. Scattered through components they
 * drift: one says "reviewed and closed", the next says "resolved", and the second one is a claim
 * this design refuses to make.
 *
 * **The one claim never made here.** No string says a landed change fixed anything. On turns where
 * every gold table *was* licensed the engine's measured accuracy is 0.7555, so about one in four
 * complaints closed on a landed commit would still be wrong. `addressed` is the stored word,
 * `retrieval_verified` is the narrowest upgrade the free ladder licenses ("the tables needed are
 * reachable"), and `resolved` is not in the vocabulary.
 *
 * Two phrases are banned outright and neither appears below outside a negation: **"automatically"**,
 * because nothing here is chasing anything on its own, and **"will be fixed"**, because nobody
 * knows that.
 */

/** Observation states the server can send, as the wire spells them. */
export type ObservationState =
  | "open"
  | "triaged"
  | "declined"
  | "duplicate"
  | "addressed"
  | "blocked_on_a_person";

/** What a reader sees for each stored state. A badge with no sentence is what teaches
 *  an operator to ignore a queue, so every member has one. */
export const STATE_COPY: Record<ObservationState, { label: string; sentence: string }> = {
  open: {
    label: "Open",
    sentence: "Filed. Nobody has looked at it yet.",
  },
  triaged: {
    label: "Being reviewed",
    sentence: "Somebody has looked and is still deciding.",
  },
  declined: {
    label: "Closed",
    sentence: "Reviewed and closed without a change. The reason is on the row.",
  },
  duplicate: {
    label: "Folded in",
    sentence: "The same problem as another report, and it joins that one's change.",
  },
  addressed: {
    label: "Change drafted",
    sentence:
      "A change to the semantic layer has been drafted. It is not in the engine until somebody commits it.",
  },
  blocked_on_a_person: {
    label: "Waiting on a person",
    sentence: "Waiting on a person. Nothing is chasing this on its own.",
  },
};

/** Decline reasons. The reason **is** the notification — there is no badge without a sentence. */
export const DECLINE_COPY: Record<string, string> = {
  working_as_intended:
    "Reviewed and closed: the engine was right. The answer matches what is in the data.",
  not_a_corpus_problem:
    "Reviewed and closed: the data itself is wrong or missing. The semantic layer cannot fix that, and this engine is not where it gets fixed.",
  needs_a_schema_change:
    "Reviewed and closed: answering this needs a table or column that does not exist in the warehouse. Someone has to build it first.",
  engine_defect:
    "Reviewed and closed as a defect in the engine, not the semantic layer. It has been written down where engine defects are written down.",
  out_of_scope: "Reviewed and closed: this is not a question this engine is meant to answer.",
  cannot_reproduce:
    "Reviewed and closed: asked again against the corpus running now, it answered correctly. If you can still reproduce it, file it again with the new answer.",
  insufficient_detail:
    "Closed without a change: there was not enough here to act on. This engine does not know who filed this report, so nobody could be asked for more.",
  wont_fix_cost:
    "Reviewed and closed: fixing this properly is more work than it is worth right now. It is a real problem and it is not being fixed.",
  dataset_defect:
    "Closed as a defect in the benchmark rather than in the engine or the semantic layer: no query over this warehouse can match the reference answer.",
};

/** Categories, in the words a reader would use. Never names a table or a column. */
export const CATEGORY_COPY: Record<string, string> = {
  wrong_value: "The number is wrong",
  wrong_scope: "It used the wrong data — wrong table, wrong filter, wrong dates",
  wrong_rows: "It counted or combined the wrong records",
  misread_question: "It answered a different question",
  term_mismatch: "A word means something else here",
  unverifiable: "Cannot tell whether this is right",
  false_refusal: "This data exists — it should have been able to answer",
  bad_clarification: "The question it asked back did not make sense",
  unclear_refusal: "Right to decline, but it should have said why",
  attempt_capped: "It ran out of attempts",
  column_suspect: "A column's values look untrustworthy",
  column_excluded: "A column should be hidden",
  reusable_fact: "A fact worth keeping",
};

/**
 * What each editable field *does*, because the two are not interchangeable.
 *
 * `summary` is indexed for retrieval and `body` is injected into the model's prompt, so an edit to
 * one changes what gets found and an edit to the other changes what the model reads. A reviewer
 * deciding whether an edit fixes a coverage miss has to know which. Nothing else is editable:
 * governance, provenance, audit and column fields are refused by `corpus/patch.py`.
 */
export const FIELD_COPY: Record<string, string> = {
  summary:
    "summary is what retrieval searches. Editing it changes which questions find this asset — which is the lever on a coverage miss.",
  body: "body is what the model reads once the asset is in context. Editing it changes how the asset is used, not whether it is found.",
};

export const REVIEW_COPY = {
  pageTitle: "Review",
  /** The product boundary in one sentence, permanently on the page. */
  pageDescription:
    "Answers and refusals somebody flagged, grouped by what looks like the same problem. Oldest first. Deciding here drafts a change to the semantic layer — it does not apply one.",

  /** Always shown under a cluster heading, because the grouping is structural. */
  clusterCaption:
    "Grouped by the kind of problem and the schema it happened in. Nothing here read the questions and decided they mean the same thing — check the rows before you treat them as one problem.",

  /** Measured, and stated so nobody reads a cluster of one as a weak signal. */
  clusterWeakness:
    "On the 73 failures imported from the v4 arm, 37 of 54 clusters hold a single observation and the largest holds three. Grouping helps about half the queue and no more.",

  queueEmpty: "Nothing to review. Every observation filed on this server has been triaged.",
  /** Deliberately a different sentence from the queue's: "nobody filed anything" and "everything
   *  is triaged" are different facts, and reading one as the other is how a queue is abandoned. */
  storeEmpty:
    "No observations. Nothing has been filed on this server, and nothing has been imported from an evaluation arm.",

  selectPrompt: "Select a cluster to see what happened and what the reference answer was.",

  /** Block 5 of the evidence panel does not exist, and the panel says why rather than omitting it
   *  silently — a missing block reads as "we did not bother" instead of "there is no data". */
  noAssetEvidence:
    "Which corpus assets were in context is not shown, because an evaluation artifact does not record it: facet_hits and pulled_in are absent from every row. Locate the asset by hand in Corpus, or re-run retrieval.",

  /** The warning that makes the held-out flag act like one. */
  heldOutWarning:
    "This question is from the held-out evaluation split. Do not copy its wording into a corpus asset — that contaminates the benchmark, and paraphrase leaks cannot be detected.",

  goldHeading: "The reference answer",
  goldCaption:
    "The statement the benchmark grades against. It is compared by fingerprint, not read, which is why it is stronger evidence than a sentence anybody could type.",

  missingTablesHeading: "Tables the reference answer needs that the turn was not allowed to read",
  missingTablesCaption:
    "The defect itself rather than a symptom. Empty means every table was reachable and the answer was still wrong, which is a different problem.",

  /* ── the decision half ─────────────────────────────────────────────────── */

  /** On the bar itself, permanently. The single most important sentence on the screen: a steward
   *  who believes a button changed the engine will stop checking whether anything landed. */
  decisionBoundary:
    "Deciding here writes a row in this server's own store. It does not change the semantic layer — that is a commit in the corpus repository, made by a person.",

  /** Shown when a patch exists but no bundle has been exported. */
  handoffPending:
    "Drafted, and not handed over. Export a bundle to get a diff somebody can apply.",

  /** Shown once a bundle exists. Names the two commands and nothing else, because a longer
   *  instruction is one somebody skims. */
  handoffExported:
    "A bundle has been exported. Applying it is git apply and a commit, run by a person in the corpus repository.",

  /** The state a landing usually reaches, and why it is not the stronger one. */
  landedMatchedNote:
    "In the corpus this server runs, alongside other changes that landed with it. Not hash-matched, because two bundles landing in one week make an exact match fail for a change that did ship.",

  /** The ladder gate on the export button. */
  ladderUnrun:
    "Nothing has verified this patch. The free checks (T0-T2) cost no model calls, so there is no reason to hand over a change nobody ran them on.",

  /** Empty-diff caption, which is a real state: a patch whose text matches what is already there. */
  diffEmpty:
    "No words changed. This patch would produce an empty diff, which means the field already holds the replacement.",

  /** The case the empty-diff caption used to swallow. It is a different fact and it gets a
   *  different sentence: the text is not the same text, and it is still not a change. */
  diffWhitespaceOnly:
    "Only whitespace differs — a newline, a blank line, or a doubled space. No word changes, so nothing retrieval searches and nothing the model reads is different. The corpus writer refuses part of this outright, because a value that does not read back as typed is not written at all. Change a word or leave the field alone.",

  /** Both the decline form and the withdraw form require one, and the requirement is the server's. */
  reasonRequired:
    "A reason is required. A closed row whose why lives only in somebody's memory gets re-opened from scratch six weeks later.",

  /** The one thing the draft form must not invite. */
  draftHeldOutWarning:
    "Write this in your own words. Copying the question's wording into an asset contaminates the benchmark, and the export refuses a verbatim run of five words or more.",

  draftHeading: "Draft a change",
  draftSubmit: "Draft this change",
  diffHeading: "What this change does",

  /* ── the reproducer (T3) ───────────────────────────────────────────────── */

  reproduceHeading: "Does this still happen?",

  /** `--embed` is not optional and the sentence says why. */
  reproduceHow:
    "Re-route this question through the engine with the answering model off. It costs nothing — the vector cache is warm — and it takes about twenty seconds. Run it with --embed: a lexical-only check has a different coverage ceiling, and one observation recorded with a single missing table came back with two that way.",

  /** The claim, and the number that bounds it. The one sentence between a green check and
   *  somebody reading it as "fixed". */
  reproduceClaim:
    "The tables the reference answer reads are reachable again. Not that the answer is right: on turns where every table was already reachable, measured accuracy is 0.7555, so about one in four would still come back wrong.",

  /** The two cases the check cannot answer, in the CLI's own words. */
  reproduceNoGold:
    "There is no reference answer on this row, so there is nothing for a coverage check to compare against. Somebody filed this by hand rather than importing it from an evaluation.",
  reproduceNotCoverage:
    "Every table the reference answer reads was already reachable when this was filed, so this was never a coverage failure. A coverage check cannot say anything about it — the free ladder stops here.",
} as const;
