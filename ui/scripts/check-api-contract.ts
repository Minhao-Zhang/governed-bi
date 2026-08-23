/**
 * Validate every GET route of a **live** engine against the client's real zod schemas.
 *
 * "Every" is checked against `../docs/openapi.json`, the inventory of record: every `GET` is
 * covered below — fourteen until the return path added `/observations` in its two shapes, so
 * sixteen — with `/livez` covered by the reachability probe in `main()` rather than by a case.
 *
 * The write verbs are out of scope on purpose, and now there are two of them
 * (`POST /turns/{id}/raised`, `PATCH /observations/{id}`). This checker only reads, so running it
 * cannot file an observation against somebody's turn as a side effect. That exclusion costs
 * something real and is worth naming: the 201 envelope those routes return is verified by
 * `tests/api/test_the_spec_matches_the_server.py` against `docs/openapi.json`, on the Python side,
 * and **not** against the client's zod schema — so a client/server disagreement on the *write*
 * response is the one shape neither checker sees.
 *
 *     npm run check:api                    # against http://127.0.0.1:2024
 *     LANGGRAPH_URL=http://host:port npm run check:api
 *
 * Why this exists. Every response crosses `parse()` in `lib/api-client.ts`, which
 * `safeParse`s and **throws** on a mismatch. So a server that returns 200 with a shape the
 * client does not declare produces a blank page, not a partial one — and nothing on either
 * side notices, because the server's own tests assert status codes and the client's schemas
 * are only exercised by a running browser. That gap has cost this project five tabs:
 * Relationships, Tables, the namespace rail, the audit list, and the semantic graph (which
 * returned 200 with 107 `kind: "column"` nodes against an enum of seven other kinds).
 *
 * It also reports **silently dropped keys**. `z.object` strips what it does not declare, so a
 * server field the client spells differently is discarded with no error anywhere — the page
 * renders, missing data, looking fine. That is the quieter half of the same defect and the
 * reason `n_nodes`/`n_edges` once read as zero on a graph of 216 edges.
 *
 * This is a checker, not a test suite: it needs a live engine and a loaded corpus, so it
 * belongs next to `npm run lint` rather than in CI.
 */

import type { z } from "zod";

import {
  assetListSchema,
  auditCorpusSchema,
  auditTraceSchema,
  auditTurnsSchema,
  capabilitiesSchema,
  columnRelatedResponseSchema,
  corpusFieldsSchema,
  corpusRowsSchema,
  erGraphSchema,
  knowledgeGraphSchema,
  observationClustersSchema,
  observationsSchema,
  pendingQueueSchema,
  schemaSummaryResponseSchema,
  tableViewSchema,
} from "../lib/schemas.ts";

const BASE = process.env.LANGGRAPH_URL ?? "http://127.0.0.1:2024";

// No credential is read or sent: the engine dropped transport auth, so `.env.local` holds
// nothing this checker needs and every route below is reachable unauthenticated.

type Case = { path: string; schema: z.ZodType<unknown>; note?: string };

/** Keys the server sends that the client's schema strips. Recurses one level into arrays and
 * the handful of container fields, which is where every real instance has been. */
function droppedKeys(raw: unknown, parsed: unknown, trail = ""): string[] {
  if (Array.isArray(raw) && Array.isArray(parsed)) {
    return raw.length && parsed.length ? droppedKeys(raw[0], parsed[0], `${trail}[0]`) : [];
  }
  if (!raw || !parsed || typeof raw !== "object" || typeof parsed !== "object") return [];
  const out: string[] = [];
  const p = parsed as Record<string, unknown>;
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const here = trail ? `${trail}.${key}` : key;
    if (!(key in p)) {
      out.push(here);
      continue;
    }
    if (value && typeof value === "object") out.push(...droppedKeys(value, p[key], here));
  }
  return out;
}

async function fetchJson(path: string): Promise<{ status: number; body: unknown }> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { accept: "application/json" },
  });
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  return { status: res.status, body };
}

/** Discover the ids the per-item routes need, so the checker works on any corpus. */
async function discoverCases(): Promise<Case[]> {
  const cases: Case[] = [
    { path: "/capabilities", schema: capabilitiesSchema },
    { path: "/schema/summary", schema: schemaSummaryResponseSchema },
    { path: "/graph", schema: erGraphSchema, note: "unscoped: the landing view" },
    { path: "/graph?node_budget=150&radius=1", schema: erGraphSchema },
    { path: "/knowledge-graph", schema: knowledgeGraphSchema, note: "unscoped: the landing view" },
    { path: "/knowledge-graph?node_budget=150&radius=1", schema: knowledgeGraphSchema },
    { path: "/corpus/assets", schema: assetListSchema },
    { path: "/corpus/fields?type=table", schema: corpusFieldsSchema },
    { path: "/corpus/rows?type=table&order=asc&offset=0&limit=5", schema: corpusRowsSchema },
    { path: "/audit/corpus", schema: auditCorpusSchema },
    { path: "/audit/turns?limit=5", schema: auditTurnsSchema },
    // The newest route, and the one this checker existed for two weeks without covering. The
    // client calls it (`api.pendingClarifications`) and parses it with `pendingQueueSchema`,
    // so an undeclared field here is the same blank `/pending` page as every defect above.
    { path: "/clarifications/pending?limit=5&offset=0", schema: pendingQueueSchema },
    // The return path (ADR 0015). Both shapes, because the grouped one is not the flat one with a
    // wrapper: it carries `n_distinct_questions` and the members' *intersection* of missing tables,
    // neither of which the flat row has. A route with no case here is a route nothing verifies.
    {
      path: "/observations?limit=5&offset=0",
      schema: observationsSchema,
      note: "empty until something is filed or an eval arm is imported",
    },
    {
      path: "/observations?group=cluster&state=open,triaged&limit=20",
      schema: observationClustersSchema,
    },
  ];

  // Discover ids from the lean catalog: the flat /schema dump is deleted.
  const { body: catalog } = await fetchJson("/schema/summary?limit=1");
  const items = (catalog as { items?: unknown[] } | null)?.items;
  const first = Array.isArray(items) ? (items[0] as Record<string, unknown> | undefined) : undefined;
  const tableId = typeof first?.id === "string" ? first.id : null;
  if (tableId) {
    cases.push({ path: `/schema/${encodeURIComponent(tableId)}`, schema: tableViewSchema });
    const schemaName = typeof first?.schema === "string" ? first.schema : null;
    if (schemaName) {
      cases.push({
        path: `/knowledge-graph?schema=${encodeURIComponent(schemaName)}&radius=1&node_budget=150`,
        schema: knowledgeGraphSchema,
      });
      cases.push({
        path: `/graph?schema=${encodeURIComponent(schemaName)}&radius=1&node_budget=150`,
        schema: erGraphSchema,
      });
    }
    const columns = Array.isArray(first?.columns) ? first.columns : [];
    const col = columns[0] as Record<string, unknown> | undefined;
    const columnId = typeof col?.id === "string" ? col.id : null;
    if (columnId) {
      cases.push({
        path: `/columns/${encodeURIComponent(columnId)}/related`,
        schema: columnRelatedResponseSchema,
      });
    }
  }

  // No `/search` case: the route was deliberately never built (ADR 0009 Amendment 1) and
  // `capabilities_for` hardcodes `can_search: false`, so the `caps` probe that used to gate
  // one here could never fire.

  const turns = (await fetchJson("/audit/turns?limit=1")).body as Record<string, unknown> | null;
  const turnList = Array.isArray(turns?.turns) ? turns.turns : [];
  const turnId = (turnList[0] as Record<string, unknown> | undefined)?.turn_id;
  if (typeof turnId === "string") {
    cases.push({
      path: `/audit/turns/${encodeURIComponent(turnId)}/trace`,
      schema: auditTraceSchema,
    });
  }

  return cases;
}

async function main(): Promise<void> {
  // `/livez` and not `/health`, which is deleted — and `/livez` is the better probe anyway:
  // it deliberately does not touch the session, so "is there an engine here" cannot be
  // answered "no" by a corpus that takes 30 seconds to load.
  const reachable = await fetch(`${BASE}/livez`).catch(() => null);
  if (!reachable) {
    console.error(`No engine at ${BASE}. Start one, or set LANGGRAPH_URL.`);
    process.exit(2);
  }

  const cases = await discoverCases();
  let failed = 0;
  let dropping = 0;
  const skipped: string[] = [];

  for (const { path, schema, note } of cases) {
    const { status, body } = await fetchJson(path);
    if (status !== 200) {
      // A 404 on a discovered id is a corpus fact, not a contract break; anything else is.
      if (status === 404) {
        skipped.push(`${path} -> 404`);
        continue;
      }
      failed += 1;
      console.error(`FAIL ${path} -> HTTP ${status}`);
      continue;
    }
    const result = schema.safeParse(body);
    if (!result.success) {
      failed += 1;
      const issues = result.error.issues
        .slice(0, 4)
        .map((i) => `      ${i.path.join(".") || "(root)"}: ${i.message}`)
        .join("\n");
      const more = result.error.issues.length > 4 ? `\n      … ${result.error.issues.length - 4} more` : "";
      console.error(`FAIL ${path}${note ? `  (${note})` : ""}\n${issues}${more}`);
      continue;
    }
    const lost = droppedKeys(body, result.data);
    if (lost.length) {
      dropping += 1;
      console.warn(`DROP ${path} — client discards: ${[...new Set(lost)].slice(0, 8).join(", ")}`);
    } else {
      console.log(`ok   ${path}${note ? `  (${note})` : ""}`);
    }
  }

  if (skipped.length) console.log(`\nskipped (absent from this corpus): ${skipped.join(", ")}`);
  console.log(
    `\n${cases.length - failed - skipped.length}/${cases.length - skipped.length} routes parse; ` +
      `${failed} fail, ${dropping} drop server fields.`,
  );
  // Dropped keys are a warning: some are deliberate (the client's lean node shape). Only a
  // parse failure is a broken page, so only that fails the check.
  process.exit(failed ? 1 : 0);
}

await main();
