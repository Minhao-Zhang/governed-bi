/**
 * The client refuses what the server refuses, for the two patch fields a steward types by hand.
 *
 * `src/governed_bi/feedback/validate.py::_patch_problems` is the authority. It applies two rules to
 * a corpus content hash, in order: the length must be 64, and *then* it must be hex. The comment
 * beside them records that the hex half was once reachable only when the length happened to be
 * right -- the same bug, already shipped once on the server.
 *
 * The form was checking length alone, so `"z".repeat(64)` was submittable from the browser and
 * refused by the engine. A client that accepts what the server rejects teaches its user that the
 * error is the engine's.
 *
 * Run: `npm run check:patch-fields`
 */
import { patchHashProblem } from "../lib/patch-fields.ts";

let failures = 0;
function check(label: string, got: string | null, want: "ok" | "refused"): void {
  const actual = got === null ? "ok" : "refused";
  if (actual !== want) {
    console.error(`FAIL ${label} -- wanted ${want}, got ${actual}${got ? `: ${got}` : ""}`);
    failures += 1;
  }
}

const HEX64 = "a".repeat(64);
const REAL = "30872d3f".repeat(8);

check("a real 64-character digest is accepted", patchHashProblem(HEX64), "ok");
check("a mixed-case-free hex digest is accepted", patchHashProblem(REAL), "ok");
check("an empty field is not an error yet", patchHashProblem(""), "ok");

check("the 16-character prefix is refused", patchHashProblem(HEX64.slice(0, 16)), "refused");
check("63 characters is refused", patchHashProblem(HEX64.slice(0, 63)), "refused");
check("65 characters is refused", patchHashProblem(HEX64 + "a"), "refused");
check("64 non-hex characters is refused", patchHashProblem("z".repeat(64)), "refused");
check("64 characters with one non-hex is refused", patchHashProblem(HEX64.slice(0, 63) + "g"), "refused");
check("uppercase hex is refused", patchHashProblem("A".repeat(64)), "refused");

// The two rules must be distinguishable, because they tell the steward different things to do:
// a short hash means "you pasted the display prefix", a non-hex one means "that is not a digest".
const short = patchHashProblem(HEX64.slice(0, 16));
const nonHex = patchHashProblem("z".repeat(64));
if (short !== null && nonHex !== null && short === nonHex) {
  console.error("FAIL the length and hex refusals say the same thing, so the fix is not implied");
  failures += 1;
}
if (short !== null && !short.includes("16")) {
  console.error(`FAIL the length refusal does not name the length it got: ${short}`);
  failures += 1;
}

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("all checks passed");
