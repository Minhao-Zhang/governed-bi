/**
 * Red/green check: the word diff the review surface renders a patch with.
 *
 *     node --experimental-strip-types scripts/check-asset-diff.ts
 *
 * Hermetic, like `check:answer-delivery` and `check:stream-messages`: it imports
 * `lib/asset-diff.ts` and needs no engine, no corpus and no network.
 *
 * **Why this is worth a check at all.** The diff is the whole of what a reviewer looks at before
 * deciding whether a change to the semantic layer is right, and a diff algorithm degrades
 * *silently* — a greedy walk that marks a whole sentence changed when one word moved still renders,
 * still looks like a diff, and quietly costs the reviewer the ability to see the edit. So the
 * property pinned here is minimality, not "it produced spans".
 *
 * The three shapes that matter: a word appended (the common case for a coverage-miss fix), a word
 * replaced mid-sentence, and identical text — where the honest answer is *no* change spans, because
 * `diffEmpty` is a real state the surface has a sentence for.
 */

import {
  classifyEdit,
  diffSize,
  diffWords,
  type DiffSpan,
  type EditKind,
} from "../lib/asset-diff.ts";

let failed = false;

function check(condition: boolean, label: string): void {
  if (condition) {
    console.log(`ok   ${label}`);
    return;
  }
  failed = true;
  console.error(`FAIL ${label}`);
}

function render(spans: DiffSpan[]): string {
  return spans
    .map((s) => (s.op === "same" ? s.text : `${s.op === "added" ? "+" : "-"}[${s.text}]`))
    .join(" ");
}

/* ── the property that matters: minimality ─────────────────────────────────── */

const appended = diffWords(
  "zip_congress maps a zip code to a congressional district.",
  "zip_congress maps a zip code to a congressional district. Questions about a district read this table.",
);
check(
  diffSize(appended).removed === 0,
  `appending removes nothing (got ${diffSize(appended).removed}): ${render(appended)}`,
);
check(diffSize(appended).added === 7, `appending adds the 7 new words: ${render(appended)}`);

const replaced = diffWords("one row per order", "one row per placed order");
check(
  diffSize(replaced).added === 1 && diffSize(replaced).removed === 0,
  `a word inserted mid-sentence is +1 -0, not a rewrite: ${render(replaced)}`,
);

const swapped = diffWords("one row per order", "one row per shipment");
check(
  diffSize(swapped).added === 1 && diffSize(swapped).removed === 1,
  `a substitution is +1 -1: ${render(swapped)}`,
);

/* ── identical text is not "changed" ───────────────────────────────────────── */

const same = diffWords("one row per order", "one row per order");
check(
  same.every((s) => s.op === "same"),
  `identical text produces no change spans: ${render(same)}`,
);
check(
  diffSize(same).added === 0 && diffSize(same).removed === 0,
  "identical text sizes to zero, which is what diffEmpty is shown for",
);

/* ── whitespace is not content ─────────────────────────────────────────────── */

const rewrapped = diffWords("one row\nper order", "one   row per\norder");
check(
  diffSize(rewrapped).added === 0 && diffSize(rewrapped).removed === 0,
  `re-wrapping is not a change -- a YAML folded scalar re-wraps on write: ${render(rewrapped)}`,
);

/* ── the three cases the surface must not collapse ──────────── */

/* A whitespace-only replacement is +0 -0 words, which is true and is not the whole answer. The
 * surface used to show it the `diffEmpty` sentence -- "the field already holds the replacement" --
 * and leave the submit button enabled, so the steward was told nothing changed and then allowed to
 * submit it as a change. `classifyEdit` is the one rule that separates the three, and both the
 * caption and the gate read it. */

const WAS = "one row per order";
const TRAILING_NEWLINE = `${WAS}\n`;
const DOUBLED_SPACE = "one row per  order";
const LEADING_BLANK_LINE = `\n${WAS}`;

check(
  classifyEdit(WAS, WAS) === "identical",
  `identical text classifies as identical (got ${classifyEdit(WAS, WAS)})`,
);
check(
  classifyEdit(WAS, TRAILING_NEWLINE) === "whitespace_only",
  `a trailing newline is whitespace_only, not identical (got ${classifyEdit(WAS, TRAILING_NEWLINE)})`,
);
check(
  classifyEdit(WAS, DOUBLED_SPACE) === "whitespace_only",
  `a doubled space is whitespace_only (got ${classifyEdit(WAS, DOUBLED_SPACE)})`,
);
check(
  classifyEdit(WAS, LEADING_BLANK_LINE) === "whitespace_only",
  `a leading blank line is whitespace_only (got ${classifyEdit(WAS, LEADING_BLANK_LINE)})`,
);
check(
  classifyEdit(WAS, "one row per shipment") === "words_changed",
  `a substituted word is words_changed (got ${classifyEdit(WAS, "one row per shipment")})`,
);
check(
  classifyEdit("", "a new summary") === "words_changed",
  "filling an empty field is words_changed",
);

/* The gate the draft form applies, stated once here so a regression in it is red rather than a
 * button somebody clicks. Only `words_changed` is submittable. */
const submittable = (was: string, becomes: string): boolean =>
  classifyEdit(was, becomes) === "words_changed";

check(
  !submittable(WAS, TRAILING_NEWLINE),
  "a whitespace-only replacement is not submittable -- the diff reports no words changed, so a form that accepted it drafts a patch with no content",
);
check(!submittable(WAS, WAS), "an identical replacement is not submittable");
check(
  submittable(WAS, "one row per placed order"),
  "an added word is submittable, which is the case the guard must not catch",
);


/* ── the empty cases, because a patch form starts empty ────────────────────── */

check(diffWords("", "").length === 0, "two empty strings diff to nothing rather than to one span");
check(
  diffSize(diffWords("", "a new summary")).added === 3,
  "an empty `was` is three additions and no removals",
);
check(
  diffSize(diffWords("the old summary", "")).removed === 3,
  "an empty `becomes` is three removals",
);

/* ── runs are merged, so a render is one element per run ───────────────────── */

const runs = diffWords("a b c d", "a x y d");
check(
  runs.filter((s) => s.op === "removed").length === 1,
  `two consecutive removals merge into one span: ${render(runs)}`,
);
check(
  runs.filter((s) => s.op === "added").length === 1,
  `two consecutive additions merge into one span: ${render(runs)}`,
);

/* ── the order a reviewer reads ────────────────────────────────────────────── */

const ordered = diffWords("keep gone keep", "keep new keep");
const middle = ordered.slice(1, 3).map((s) => s.op);
check(
  middle[0] === "removed" && middle[1] === "added",
  `a removal is emitted before the addition that replaces it: ${render(ordered)}`,
);


// ── punctuation is part of a word ─────────────────────────────────────────────
//
// `tokenize` splits on whitespace and nothing else. Stripping punctuation as well survived every
// check here, and with `classifyEdit` in place the consequence got worse than a wrong word count:
// deleting a trailing period would classify as `whitespace_only`, so the submit control would refuse
// a legitimate edit and the caption would say nothing changed.
//
// It is not obviously wrong to strip it -- a reviewer might argue re-punctuating is not a change.
// These cases exist so that argument has to be made out loud rather than arriving as a one-line
// tidy-up in the tokenizer.
{
  const cases: Array<[string, string, string, EditKind]> = [
    ["a deleted period is a change", "Grain is one order.", "Grain is one order", "words_changed"],
    ["an added period is a change", "Grain is one order", "Grain is one order.", "words_changed"],
    [
      "a comma becoming a semicolon is a change",
      "orders, one per row",
      "orders; one per row",
      "words_changed",
    ],
    [
      "a question mark added to a clause is a change",
      "how many orders",
      "how many orders?",
      "words_changed",
    ],
  ];
  for (const [label, was, becomes, want] of cases) {
    const got = classifyEdit(was, becomes);
    check(got === want, `${label} (wanted ${want}, got ${got})`);
  }
}

if (failed) {
  console.error("\nasset-diff check FAILED");
  process.exit(1);
}
console.log("\nall checks passed");
