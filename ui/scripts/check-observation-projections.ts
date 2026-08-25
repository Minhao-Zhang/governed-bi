/**
 * The client accepts **both** projections the engine can serve for an observation.
 *
 * `api/feedback_routes.py::PUBLIC_OBSERVATION_FIELDS` is the authority. The engine has two shapes
 * for one row and the switch is deployment-time, not per-request: with
 * `GOVERNED_BI_FEEDBACK_ADMIN` set it projects every field, and without it only the allowlist
 * below. A client schema that requires a field outside the allowlist cannot parse a reader-mode
 * response at all.
 *
 * That shipped: `gold_sql`, `gold_fingerprint` and `pred_fingerprint` were `.nullable()` without
 * `.optional()`, so a 73-row queue came back as 219 zod issues rendered to the operator as
 * "response did not match the expected schema" — a message that names neither the fields nor the
 * deployment flag behind them. `docs/openapi.json` had the three out of `required` the whole time;
 * this schema was the half that disagreed with the contract.
 *
 * Field *values* come from `docs/openapi.json` rather than from a hand-written row, so a property
 * whose type changes there is exercised here without anyone remembering to update this file. The
 * one hardcoded thing is the allowlist, and
 * `tests/api/test_the_client_accepts_both_wire_projections.py` fails if it drifts from the Python.
 *
 * Run: `npm run check:observation-projections`
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { observationSchema } from "../lib/schemas.ts";

/** Mirrors `PUBLIC_OBSERVATION_FIELDS`. Pinned from the Python side; do not edit one alone. */
const READER_FIELDS: readonly string[] = [
  "arm", "blocked_note", "category", "corpus_content_hash", "db_id", "decline_reason",
  "duplicate_of", "filed_at", "generated_sql", "kind", "licensed", "missing_tables", "note",
  "observation_id", "open", "outcome", "question", "question_id", "question_is_held_out",
  "quality_flags", "refused_by", "schemas", "source", "state", "thread_id", "turn_id",
];

const here = dirname(fileURLToPath(import.meta.url));
/** Only the parts of the spec this check reads. Narrower than OpenAPI and deliberately so: a
 *  wider type here would be a claim about the whole document that nothing verifies. */
type SpecProperty = { type?: string; enum?: unknown[] };
type Spec = {
  components: { schemas: { ObservationResponse: { properties: Record<string, SpecProperty> } } };
};

const spec = JSON.parse(
  readFileSync(join(here, "..", "..", "docs", "openapi.json"), "utf8"),
) as Spec;
const props = spec.components.schemas.ObservationResponse.properties;

/** A value the contract permits for this property. `enum[0]` rather than a made-up string, because
 *  the zod schemas use `z.enum` and a plausible-looking wrong value would fail for the wrong
 *  reason. */
function sample(name: string, p: SpecProperty): unknown {
  if (Array.isArray(p.enum) && p.enum.length > 0) return p.enum[0];
  switch (p.type) {
    case "array": return [];
    case "boolean": return false;
    case "number":
    case "integer": return 0;
    case "object": return {};
    default: return `sample-${name}`;
  }
}

function row(fields: readonly string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    if (!(f in props)) throw new Error(`${f} is not a property of ObservationResponse in the spec`);
    out[f] = sample(f, props[f]);
  }
  return out;
}

let failures = 0;
for (const [label, fields] of [
  ["reader mode (GOVERNED_BI_FEEDBACK_ADMIN unset)", READER_FIELDS],
  ["steward mode (every property in the spec)", Object.keys(props)],
] as const) {
  const r = observationSchema.safeParse(row(fields));
  if (r.success) {
    console.log(`ok   ${label} — ${fields.length} fields`);
  } else {
    failures += 1;
    const missing = [...new Set(r.error.issues.map((i) => i.path.join(".")))];
    console.error(`FAIL ${label} — the client requires ${missing.length} field(s) it was not sent:`);
    for (const m of missing) console.error(`       ${m}`);
  }
}

// A field the allowlist withholds must be one the client can do without. Guards the case where
// somebody makes a withheld field required *and* adds it to the allowlist in the same breath.
const withheld = Object.keys(props).filter((f) => !READER_FIELDS.includes(f));
if (withheld.length === 0) {
  failures += 1;
  console.error("FAIL the allowlist withholds nothing, so this check proves nothing");
} else {
  console.log(`ok   ${withheld.length} field(s) withheld in reader mode: ${withheld.join(", ")}`);
}

if (failures > 0) {
  console.error(`\n${failures} check(s) failed.`);
  process.exit(1);
}
console.log("\nboth projections parse.");
