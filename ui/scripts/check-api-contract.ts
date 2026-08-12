/**
 * Validate every GET route of a **live** engine against the client's real zod schemas.
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

import { readFileSync } from "node:fs";

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
  schemaSummaryResponseSchema,
  searchResponseSchema,
  tableViewSchema,
} from "../lib/schemas.ts";

const BASE = process.env.LANGGRAPH_URL ?? "http://127.0.0.1:2024";

/**
 * Transport auth, which this checker did not send and so could not check.
 *
 * The engine grew a required `x-api-key` on 2026-08-10 and every route here answered 401 from
 * then on — reported as `0/11 routes parse`, eleven contract failures that were all the same
 * missing header. A checker that fails identically on every input is not measuring the contract,
 * which is the failure this file's own header describes on the other side of the wire.
 *
 * Node does not read `.env.local`; that is Next's job, and this runs under plain `node
 * --experimental-strip-types`. So the file is parsed here, and only for keys the environment has
 * not already set — an explicit `NEXT_PUBLIC_GOVERNED_BI_API_KEY=… npm run check:api` still wins.
 * `../.env`'s `GOVERNED_BI_API_KEY` is deliberately NOT read: it holds the engine's secrets, and a
 * frontend script reaching into it would make the two configurations one, which they are not.
 */
function loadEnvLocal(): void {
  const path = new URL("../.env.local", import.meta.url);
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return;
  }
  for (const line of text.split(/\r?\n/)) {
    const match = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
    if (!match || line.trimStart().startsWith("#")) continue;
    const [, key, rawValue] = match;
    if (process.env[key] !== undefined) continue;
    process.env[key] = rawValue.trim().replace(/^["'](.*)["']$/, "$1");
  }
}

loadEnvLocal();

// Dynamic, and after `loadEnvLocal()`: `lib/env.ts` reads `process.env` at module scope, so a
// static import would be evaluated before the file is parsed and `API_KEY` would be "". Importing
// it rather than re-spelling `x-api-key` here keeps one definition of how this client authenticates.
const { authHeaders, API_KEY } = await import("../lib/env.ts");

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
    headers: { accept: "application/json", ...authHeaders() },
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

  const caps = (await fetchJson("/capabilities")).body as Record<string, unknown> | null;
  if (caps?.can_search) cases.push({ path: "/search?q=a", schema: searchResponseSchema });

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

  // Exit 2 (a precondition), not 1 (a contract failure). Without the key every route below
  // answers 401 identically, and reporting that as eleven schema mismatches is worse than
  // reporting nothing: it names the payloads as the problem and hides the one real cause.
  if (!API_KEY) {
    console.error(
      "No NEXT_PUBLIC_GOVERNED_BI_API_KEY (checked the environment, then ../.env.local). " +
        "Every route but /livez answers 401 without it, so nothing here would be a contract result.",
    );
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
