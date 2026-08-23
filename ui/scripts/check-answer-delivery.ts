/**
 * Red/green check: the two reader-facing translations on the answer card.
 *
 *     node --experimental-strip-types scripts/check-answer-delivery.ts
 *
 * There is no test runner in `ui/` — `scripts/check-api-contract.ts` and
 * `scripts/check-stream-messages.ts` are the whole convention, and this follows it rather than
 * adding a framework. It runs as `npm run check:answer-delivery`. Like `check:stream-messages`
 * it is hermetic: it imports `lib/answer-delivery.ts` and needs no engine, no corpus and no
 * network, so it is safe to run in CI.
 *
 * What is pinned here is the part that can silently degrade: the `no_sql` split (one token, two
 * materially different turns), the fall-through that keeps an unrecognised terminal visible, and
 * the requirement that every member of `CATALOG_GLIMPSE_REFUSALS` also has a sentence in
 * `REFUSED_BY_SENTENCE` — the glimpse is appended to that sentence, so a member without one
 * renders a bare catalog under the unrecognised-value fallback.
 */

import {
  CATALOG_GLIMPSE_REFUSALS,
  PHRASED_REFUSALS,
  catalogGlimpse,
  terminalLabel,
  wantsCatalogGlimpse,
} from "../lib/answer-delivery.ts";

let failed = false;

function check(condition: boolean, label: string): void {
  if (condition) {
    console.log(`ok   ${label}`);
    return;
  }
  failed = true;
  console.error(`FAIL ${label}`);
}

/* ── terminalLabel ─────────────────────────────────────────────────────────── */

const passed = [{ passed: true, reason_code: "passed" }];

// The split is the whole reason `terminalLabel` takes `attempts`. Collapsing the two would give
// the stronger claim the softer phrasing, which is the defect the fork's 2026-08-16 run found.
check(
  terminalLabel("no_sql", []) === "answered without consulting your data at all",
  "an empty ledger under `no_sql` gets the never-touched-the-data phrasing",
);
check(
  terminalLabel("no_sql", passed) === "answered from a definition, without running a query",
  "a non-empty ledger under `no_sql` gets the sampled-then-recited phrasing",
);
check(
  terminalLabel("no_sql", []) !== terminalLabel("no_sql", passed),
  "the two `no_sql` turns do not read identically",
);

// Every token in `govern/ledger.py::ExecutionRecord.terminal` must translate, or a raw engine word
// reaches a business reader. `crashed` is the one the fork's map omits.
for (const terminal of ["answered", "graded", "capped", "refused", "crashed"]) {
  const label = terminalLabel(terminal, passed);
  check(
    label !== null && label !== terminal,
    `\`${terminal}\` is translated, not passed through raw (got ${JSON.stringify(label)})`,
  );
}

// Fall-through, not omission: `reliability-stamp.tsx`'s principle is that an unfamiliar state
// should be visible. A `null` here would delete it from the page.
check(
  terminalLabel("some_future_terminal", passed) === "some_future_terminal",
  "an unrecognised terminal falls through to the raw token",
);
// No terminal at all is a different fact from an unrecognised one, and renders nothing.
check(terminalLabel(null) === null, "a missing terminal produces no sentence");
check(
  terminalLabel("answered") === "ran a query against your data",
  "`attempts` is optional for a terminal that does not need it",
);

/* ── the catalog glimpse ───────────────────────────────────────────────────── */

for (const reason of CATALOG_GLIMPSE_REFUSALS) {
  check(
    PHRASED_REFUSALS.includes(reason),
    `\`${reason}\` has a sentence in REFUSED_BY_SENTENCE to append the glimpse to`,
  );
  check(wantsCatalogGlimpse(reason), `\`${reason}\` asks for the glimpse`);
}

// `guard` is the measured case (2026-08-19, two SOW questions, one answerable). If it ever falls
// out of the set, this port has lost the evidence it was justified by.
check(
  CATALOG_GLIMPSE_REFUSALS.includes("guard"),
  "`guard` is in the set — it is the refusal the 2026-08-19 false-refusal measurement came from",
);

// The reasons that found their tables, refused a written statement, or were our own bug must not
// fetch the catalog: the glimpse gates a network request as well as a render.
for (const reason of [
  "missing_join_path",
  "over_connect_bounds",
  "licensed_table_evicted",
  "guardrail",
  "attempt_cap",
  "negative_example",
  "retrieval_channel_failed",
  "model_error",
  "guardrail_error",
]) {
  check(!wantsCatalogGlimpse(reason), `\`${reason}\` does not fetch the catalog`);
}
check(!wantsCatalogGlimpse(null), "an answer with no `refused_by` does not fetch the catalog");
check(
  !wantsCatalogGlimpse("some_future_reason"),
  "an unrecognised refusal reason does not fetch the catalog",
);

// An empty catalog renders nothing rather than "The tables we can see: ." — a corpus with no
// visible tables is a real state (everything excluded) and the sentence would be a lie about it.
check(catalogGlimpse([]) === null, "an empty catalog produces no glimpse");
check(
  catalogGlimpse(["orders", "invoices"]) === "The tables we can see: orders, invoices.",
  "a short catalog is named in full",
);
// Truncation has to be audible: the caller only ever holds the first page of `/schema/summary`.
const many = ["a", "b", "c", "d", "e", "f", "g"];
check(
  catalogGlimpse(many) === "Some of the tables we can see: a, b, c, d, e.",
  "a long catalog is capped and says so",
);
check(
  catalogGlimpse(many, 2) === "Some of the tables we can see: a, b.",
  "the cap is the caller's to lower",
);

if (failed) {
  process.exit(1);
}
console.log("\nall checks passed");
