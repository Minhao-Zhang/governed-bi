/**
 * Red/green check: the return path's copy module says what the engine can support, and says it
 * once.
 *
 *     node --experimental-strip-types scripts/check-review-copy.ts
 *
 * There is no test runner in `ui/` — `scripts/check-answer-delivery.ts` is the convention and
 * this follows it. It runs as `npm run check:review-copy`. Hermetic: it imports
 * `lib/review-copy.ts` and reads two repository files as text, so it needs no engine, no corpus
 * and no network.
 *
 * `docs/return-path.md` listed this file as the half of the honest-copy rule that was never
 * built: every string lives in one module, "which is what makes the rule checkable at all — but
 * the check is not written". Three things it pins.
 *
 * **1. Coverage.** Every member of the wire's `ObservationState`, `DeclineReason` and `Category`
 * unions has a string. A badge with no sentence is what teaches an operator to ignore a queue,
 * and the failure mode is silent: an enum member added on the engine side renders as a raw
 * `snake_case` token under a `Record` lookup that returns `undefined`. The enums are read out of
 * `src/governed_bi/feedback/events.py` rather than restated here, so adding a member to the
 * engine fails this check instead of agreeing with it.
 *
 * **2. Banned phrases.** `automatically` and `will be fixed` outside a negation, because nothing
 * here chases anything on its own and nobody knows that. Plus `robust`, `seamless` and
 * `comprehensive`, which are claims with no measurement behind them.
 *
 * **3. The accuracy figure, against its one source.** This is the check that has already earned
 * itself. The pair moved 0.7555 → 0.7548 on 2026-08-24; `docs/open-work.md` claimed "all ten
 * sites are updated" and two were not, both of them here — `lib/review-copy.ts`'s module header
 * and `components/review/reproduce-panel.tsx`'s, the first of them 196 lines above a string
 * carrying the *new* figure. A comment contradicting the code in the same file is the worst
 * shape this can take, and review did not catch it twice. So the figure is not restated on this
 * side at all: it is extracted from `tools/reproduce_observation.py`'s `CLAIM`, the constant that
 * exists "because a CLI and a screen disagreeing about what a green T3 means is the two-answers
 * defect the derived states exist to avoid". Any accuracy-shaped number under the review
 * surfaces that is not the one in `CLAIM` fails here.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  CATEGORY_COPY,
  DECLINE_COPY,
  FIELD_COPY,
  REVIEW_COPY,
  STATE_COPY,
} from "../lib/review-copy.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "..", "..");

let failed = false;

function check(condition: boolean, label: string): void {
  if (condition) {
    console.log(`ok   ${label}`);
    return;
  }
  failed = true;
  console.error(`FAIL ${label}`);
}

function read(relative: string): string {
  return readFileSync(join(REPO, relative), "utf8");
}

/* ── 1. coverage, against the engine's own enums ───────────────────────────── */

/**
 * Members of a `str, Enum` class in `feedback/events.py`.
 *
 * A text scan and not an import, because this is a Node script and the enum is Python. Scoped to
 * the `class <Name>(str, Enum):` block so a `#:` comment mentioning another member's name cannot
 * widen the set. The count assertion below is the control: if this returns nothing because the
 * declaration was reformatted, the check fails rather than passing over an empty set.
 */
function pythonEnum(source: string, className: string): string[] {
  const start = source.indexOf(`class ${className}(str, Enum):`);
  if (start === -1) return [];
  const rest = source.slice(start);
  // The block ends at the next top-level `class`/`def`/`@`, which is the first line after the
  // header that starts in column zero.
  const lines = rest.split("\n").slice(1);
  const out: string[] = [];
  for (const line of lines) {
    if (/^\S/.test(line)) break;
    const m = /^\s{4}([a-z_][a-z0-9_]*)\s*=\s*"([a-z_]+)"/.exec(line);
    if (m) out.push(m[2]);
  }
  return out;
}

const events = read("src/governed_bi/feedback/events.py");

const unions: Array<[string, string, Record<string, unknown>, number]> = [
  ["ObservationState", "STATE_COPY", STATE_COPY, 6],
  ["DeclineReason", "DECLINE_COPY", DECLINE_COPY, 9],
  ["Category", "CATEGORY_COPY", CATEGORY_COPY, 13],
];

for (const [enumName, copyName, copy, floor] of unions) {
  const members = pythonEnum(events, enumName);
  check(
    members.length >= floor,
    `${enumName}: read ${members.length} members from feedback/events.py (floor ${floor}) — ` +
      `below the floor means the scan missed the declaration and every check under it is vacuous`,
  );
  const missing = members.filter((m) => !(m in copy));
  check(
    missing.length === 0,
    `${copyName} has a string for every ${enumName} member` +
      (missing.length ? ` — missing: ${missing.join(", ")}` : ""),
  );
  // The other direction: a key here that the engine cannot send is copy nobody will ever read,
  // and it is how a renamed wire value leaves its old sentence behind looking maintained.
  const orphaned = Object.keys(copy).filter((k) => !members.includes(k));
  check(
    orphaned.length === 0,
    `${copyName} has no key the engine cannot send` +
      (orphaned.length ? ` — orphaned: ${orphaned.join(", ")}` : ""),
  );
}

check(
  Object.keys(FIELD_COPY).sort().join(",") === "body,summary",
  "FIELD_COPY covers exactly the two editable field paths corpus/patch.py::EDITABLE allows",
);

/* ── 2. banned phrases ─────────────────────────────────────────────────────── */

/** Every string the module can put on a screen, flattened. */
function strings(value: unknown, out: string[] = []): string[] {
  if (typeof value === "string") out.push(value);
  else if (value && typeof value === "object") {
    for (const v of Object.values(value)) strings(v, out);
  }
  return out;
}

const copy = strings([STATE_COPY, DECLINE_COPY, CATEGORY_COPY, FIELD_COPY, REVIEW_COPY]);
check(copy.length > 40, `flattened ${copy.length} reader-facing strings (control: expect > 40)`);

// `automatically` and `will be fixed` are the two this project cares about most. A negation is
// allowed, because "this is not fixed automatically" is the honest sentence and banning the word
// outright would ban saying so.
const NEGATED = /\b(?:no|not|never|nothing|nobody|does not|will not|won't|cannot)\b/i;
const BANNED = ["automatically", "will be fixed", "robust", "seamless", "comprehensive"];

for (const phrase of BANNED) {
  const offenders = copy.filter((s) => {
    const at = s.toLowerCase().indexOf(phrase);
    if (at === -1) return false;
    // Negation has to be in the same clause, so look back to the previous sentence boundary.
    const clause = s.slice(0, at).split(/[.;]/).pop() ?? "";
    return !NEGATED.test(clause);
  });
  check(
    offenders.length === 0,
    `no reader-facing string claims "${phrase}" outside a negation` +
      (offenders.length ? `\n     ${offenders.join("\n     ")}` : ""),
  );
}

/* ── 3. the accuracy figure, against tools/reproduce_observation.py::CLAIM ─── */

const claimSource = read("tools/reproduce_observation.py");
const claimBlock = /^CLAIM = \(([\s\S]*?)\n\)$/m.exec(claimSource);
check(claimBlock !== null, "tools/reproduce_observation.py still declares CLAIM as one literal");

const claimFigures = [...(claimBlock?.[1] ?? "").matchAll(/\b0\.\d{3,4}\b/g)].map((m) => m[0]);
check(
  claimFigures.length === 2,
  `CLAIM carries both figures — the licensed-and-tableful one and the all-covered-turns one ` +
    `(found ${claimFigures.length}: ${claimFigures.join(", ")})`,
);

// Every accuracy-shaped number anywhere under the review surfaces, comments included. Comments
// are the point: that is where both stale copies of 0.7555 lived.
const SURFACES = [
  "ui/lib/review-copy.ts",
  "ui/components/review/reproduce-panel.tsx",
  "ui/components/review/review-surface.tsx",
  "ui/components/review/evidence-bundle.tsx",
  "ui/components/review/decision-bar.tsx",
  "ui/components/review/handoff-panel.tsx",
  "ui/components/review/cluster-panel.tsx",
  "ui/components/review/review-queue.tsx",
  "ui/components/review/asset-diff.tsx",
];

for (const path of SURFACES) {
  const text = read(path);
  const stray = [...text.matchAll(/\b0\.\d{3,4}\b/g)]
    .map((m) => m[0])
    .filter((f) => !claimFigures.includes(f));
  check(
    stray.length === 0,
    `${path} states no accuracy figure that CLAIM does not` +
      (stray.length ? ` — found ${[...new Set(stray)].join(", ")}, CLAIM has ${claimFigures.join(", ")}` : ""),
  );
}

if (failed) {
  console.error("\nreview copy check FAILED");
  process.exit(1);
}
console.log("\nreview copy check passed");
