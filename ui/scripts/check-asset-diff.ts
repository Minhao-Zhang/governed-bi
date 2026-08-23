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

import { diffSize, diffWords, type DiffSpan } from "../lib/asset-diff.ts";

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

if (failed) {
  console.error("\nasset-diff check FAILED");
  process.exit(1);
}
console.log("\nall checks passed");
