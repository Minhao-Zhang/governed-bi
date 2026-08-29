/**
 * The client's stage vocabulary against the engine's. Fail when they disagree.
 *
 *     npm run check:stage-vocabulary
 *
 * `register/stages.py` is the authority — `lib/steps.ts:14` says so in as many words — and
 * there is **no compiler between the two ends**. `GovEvent` exists only as a TypeScript
 * interface (`lib/steps.ts`); the Python side hand-builds the payload dict in
 * `serve/events.py::emit`. Two independent declarations of one wire format, with nothing
 * binding them.
 *
 * That cost something. The `rewrite` rail was deleted from the spine on 2026-08-26
 * (`serve/graph.py::_after_guard` records why), the event stopped arriving, and **nothing
 * broke** — which is the failure. `defaultLabel` kept a `case "rewrite"` arm producing
 * "Rewrote the follow-up", `stepIcon` kept an icon for it and `PHASE_OF` kept a phase, all
 * unreachable, all still reading to a maintainer as behaviour the engine has. Lint passed,
 * `tsc` passed, the build passed. The same silence runs the other way: a stage the engine
 * *adds* renders as its own bare wire name in the timeline, which is what `abstain`,
 * `reflect` and `narrate` did until this check was written.
 *
 * So both directions are checked, and only one of them is loud on its own:
 *
 *  - **A. No invented name.** Every stage name the client names in a declared position must
 *    be a `Stage` value. A name Python dropped renders nothing and is merely dead.
 *  - **B. No unlabelled stage.** Every `Stage` value must have client copy, or be named in
 *    `NO_CLIENT_COPY` below with the reason. This is the direction that degrades silently,
 *    and it is the one a "does every listed name exist?" check passes straight through.
 *  - **C. Degradation, not a crash.** An unrecognised name must still label, still get an
 *    icon and still classify as a rail. `steps.ts` treats `step` as unvalidated wire input
 *    on purpose (`FACET_NOUNS` is a `Map` and not an object literal for exactly that reason),
 *    and a stage from an older or newer server has to render rather than throw.
 *
 * **What this cannot see, stated because it is the case that started it.** A `Stage` member
 * outliving its graph node is invisible here: `rewrite` is still a legal member — the enum
 * keeps retired names so a turn recorded before the deletion still parses — so direction A
 * would have accepted `case "rewrite"` forever. Nothing in this file reads `serve/graph.py`,
 * and the check that would is a different one. What this catches is the vocabulary drifting,
 * which is the larger surface and the one that moves every week.
 *
 * Hermetic: it reads three repository files as text and imports `lib/steps.ts`. No engine, no
 * corpus, no network, which is why it can run in CI.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { defaultLabel, isRail, stepIcon } from "../lib/steps.ts";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const read = (...parts: string[]) => readFileSync(join(repo, ...parts), "utf8");

/* ── the authority ────────────────────────────────────────────────────────── */

/**
 * The `name = "value"` members of one `class X(str, Enum)` block in `register/stages.py`.
 *
 * Read out of the Python rather than restated here, so this file cannot be the thing that is
 * stale. It stops at the next top-level `class`/`def`/assignment, which is what keeps the
 * `Stage` body from swallowing `Outcome` and the module constants below it.
 */
function pythonEnum(source: string, name: string): string[] {
  const start = source.indexOf(`class ${name}(str, Enum):`);
  if (start < 0) throw new Error(`register/stages.py declares no \`class ${name}(str, Enum)\``);
  const rest = source.slice(start);
  const end = rest.slice(1).search(/\n(?=[A-Za-z_@#"])/);
  const body = end < 0 ? rest : rest.slice(0, end + 1);
  return [...body.matchAll(/^ {4}(\w+) = "([^"]+)"$/gm)].map((m) => m[2]);
}

const STAGES_PY = read("src", "governed_bi", "register", "stages.py");
const STAGES = new Set(pythonEnum(STAGES_PY, "Stage"));
const OUTCOMES = new Set(pythonEnum(STAGES_PY, "Outcome"));

/* ── B's declared exemptions ──────────────────────────────────────────────── */

/**
 * `Stage` values the client gives no copy to, and why. Asserted **exactly** — an entry that
 * stops being true fails as loudly as a stage that stops being covered, because a waiver
 * nobody re-reads is how a list of five becomes a list of fifteen.
 *
 * Every reason below is a property of `register/stages.py` itself, so adding a member here
 * means quoting that file rather than deciding something new.
 */
const NO_CLIENT_COPY: Record<string, string> = {
  graded_delivery:
    "no row in ADR 0010's event table: a re-execution after a passing recheck is carried " +
    "on the answer's assurance tier, not on the timeline",
  repair: "stages.py files it under `Declared, not yet emitted`",
  table_select: "stages.py: attributed after the fact by the offline analyser, never streamed",
  sql_generate: "stages.py: attributed after the fact by the offline analyser, never streamed",
};

/* ── the client's declared positions ──────────────────────────────────────── */

const STEPS_TS = read("ui", "lib", "steps.ts");
const TIMELINE_TSX = read("ui", "components", "chat", "agent-timeline.tsx");
const FIXTURES_TS = read("ui", "lib", "mock", "fixtures.ts");

/** The quoted keys of a `const NAME = new Map<string, string>([...])` block. */
function mapKeys(source: string, where: string, name: string): string[] {
  const m = new RegExp(`const ${name} = new Map<string, string>\\(\\[(.*?)\\]\\);`, "s").exec(source);
  if (!m) throw new Error(`${where} declares no \`const ${name} = new Map<string, string>\``);
  return [...m[1].matchAll(/\[\s*"([^"]+)"/g)].map((x) => x[1]);
}

/** The quoted members of a `const NAME = new Set([...])` block. */
function setMembers(source: string, where: string, name: string): string[] {
  const m = new RegExp(`const ${name} = new Set\\(\\[(.*?)\\]\\);`, "s").exec(source);
  if (!m) throw new Error(`${where} declares no \`const ${name} = new Set\``);
  return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
}

/**
 * Where the client writes a stage name down. Not a grep for string literals: a name only
 * counts as *claimed* if it sits somewhere the client dispatches on it, and the scans are
 * asserted non-empty below so a renamed constant fails rather than passing vacuously.
 */
const CLAIMED_STAGES: Record<string, string[]> = {
  "steps.ts TOOL_STEPS": setMembers(STEPS_TS, "lib/steps.ts", "TOOL_STEPS"),
  "steps.ts FACET_NOUNS": mapKeys(STEPS_TS, "lib/steps.ts", "FACET_NOUNS"),
  "agent-timeline.tsx PHASE_OF": mapKeys(TIMELINE_TSX, "components/chat/agent-timeline.tsx", "PHASE_OF"),
  "fixtures.ts MOCK_AGENT_EVENTS": [...FIXTURES_TS.matchAll(/\bstep: "([^"]+)"/g)].map((m) => m[1]),
};

/**
 * Every `case "…":` in `steps.ts`. Three switches live there and they speak **two**
 * vocabularies — `stepIcon` and `defaultLabel` dispatch on `Stage`, `outcomeLabel` on
 * `Outcome` — so an arm is checked against the union. Splitting them would mean parsing
 * function bodies to gain nothing: both enums come out of the same file, and a name in
 * neither is the defect either way.
 */
const CASE_ARMS = [...STEPS_TS.matchAll(/^\s*case "([^"]+)":/gm)].map((m) => m[1]);

/* ── run ──────────────────────────────────────────────────────────────────── */

let failures = 0;
const fail = (line: string, ...detail: string[]) => {
  failures += 1;
  console.error(`FAIL ${line}`);
  for (const d of detail) console.error(`       ${d}`);
};

// Positive control. Every assertion below is a set difference, and a set difference over an
// empty scan is vacuously clean — the way `_declared` returning `[]` would have made
// `tests/api/test_provenance_groups_match_the_register.py` pass on a renamed array.
for (const [label, names] of [
  ["Stage", [...STAGES]],
  ["Outcome", [...OUTCOMES]],
  ...Object.entries(CLAIMED_STAGES),
  ["steps.ts case arms", CASE_ARMS],
] as [string, string[]][]) {
  if (names.length === 0) fail(`parsed nothing out of ${label} — the file shape or the regex changed`);
}
for (const anchor of ["guard", "stamp", "agent_core"]) {
  if (!STAGES.has(anchor)) fail(`Stage has no \`${anchor}\` — this is not the enum this check means`);
}
if (!OUTCOMES.has("answered")) fail("Outcome has no `answered` — this is not the enum this check means");

console.log(`     register/stages.py: ${STAGES.size} stages, ${OUTCOMES.size} outcomes`);

/* A. No invented name. */
for (const [where, names] of Object.entries(CLAIMED_STAGES)) {
  const invented = [...new Set(names)].filter((n) => !STAGES.has(n)).sort();
  if (invented.length > 0) {
    fail(
      `${where} names ${invented.length} stage(s) \`register/stages.py\` does not declare:`,
      ...invented.map((n) => `${n} — delete it, or add the Stage member the engine emits`),
    );
  } else {
    console.log(`ok   ${where} — ${new Set(names).size} name(s), all declared`);
  }
}

const strayArms = [...new Set(CASE_ARMS)].filter((n) => !STAGES.has(n) && !OUTCOMES.has(n)).sort();
if (strayArms.length > 0) {
  fail(
    `lib/steps.ts switches on ${strayArms.length} name(s) that are neither a Stage nor an Outcome:`,
    ...strayArms,
  );
} else {
  console.log(`ok   steps.ts case arms — ${new Set(CASE_ARMS).size} name(s), all declared`);
}

/* B. No unlabelled stage. Asked of the imported module, not of its source: what matters is
 * what a reader sees, and a `case` arm that returns the step name would pass a source scan. */
const NOT_A_STAGE = "not-a-stage";
// Asserted rather than assumed: were a stage ever given this name, `FALLBACK_ICON` would be a
// real icon and `hasCopy` would report every stage as covered — the check would pass vacuously,
// which is the one failure a check of this kind must not have.
if (STAGES.has(NOT_A_STAGE)) {
  fail(`the sentinel ${JSON.stringify(NOT_A_STAGE)} is a declared stage; rename the sentinel`);
  process.exit(1);
}
const FALLBACK_ICON = stepIcon(NOT_A_STAGE);
const hasCopy = (step: string) =>
  defaultLabel({ step, status: "ok" }) !== step || stepIcon(step) !== FALLBACK_ICON;

const unlabelled = [...STAGES].filter((s) => !hasCopy(s)).sort();
const waived = Object.keys(NO_CLIENT_COPY).sort();
const missing = unlabelled.filter((s) => !(s in NO_CLIENT_COPY));
const stale = waived.filter((s) => !unlabelled.includes(s));
if (missing.length > 0) {
  fail(
    `${missing.length} Stage value(s) render as their own wire name in the timeline:`,
    ...missing.map((s) => `${s} — give it copy in defaultLabel/stepIcon, or declare why not`),
  );
}
if (stale.length > 0) {
  fail(
    `${stale.length} NO_CLIENT_COPY entr(ies) are stale — the client covers them now:`,
    ...stale.map((s) => `${s} — drop the waiver`),
  );
}
const unknownWaiver = waived.filter((s) => !STAGES.has(s));
if (unknownWaiver.length > 0) {
  fail(`NO_CLIENT_COPY waives ${unknownWaiver.length} name(s) that are not stages:`, ...unknownWaiver);
}
if (missing.length === 0 && stale.length === 0 && unknownWaiver.length === 0) {
  console.log(
    `ok   ${STAGES.size - waived.length} stage(s) carry client copy; ${waived.length} waived: ${waived.join(", ")}`,
  );
}

/* C. Degradation, not a crash. */
const UNKNOWN = "a_stage_from_a_newer_server";
const label = defaultLabel({ step: UNKNOWN, status: "ok" });
if (label !== UNKNOWN) {
  fail(`an unrecognised step must label as its own name, got ${JSON.stringify(label)}`);
} else if (stepIcon(UNKNOWN) !== FALLBACK_ICON || !isRail(UNKNOWN)) {
  fail("an unrecognised step must get the fallback icon and classify as a rail");
} else {
  console.log("ok   an unrecognised step degrades: bare label, fallback icon, rail");
}

if (failures > 0) {
  console.error(`\n${failures} check(s) failed.`);
  process.exit(1);
}
console.log("\nthe client and `register/stages.py` speak the same vocabulary.");
