/**
 * Typed client for the engine's custom REST routes (`../docs/openapi.json`). Every response is
 * zod-validated at the boundary and fails loud. Chat goes through `useStream`, not this. With no
 * `NEXT_PUBLIC_LANGGRAPH_URL` each call returns without fetching.
 */

import { z } from "zod";

import { LANGGRAPH_URL, USE_MOCKS } from "@/lib/env";
import { MOCK_CAPABILITIES } from "@/lib/mock/fixtures";
import { capabilitiesSchema, pendingClarificationsSchema } from "@/lib/schemas";
import type { Capabilities, PendingClarifications } from "@/lib/types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function parse<T>(path: string, schema: z.ZodType<T>, data: unknown): T {
  const parsed = schema.safeParse(data);
  if (!parsed.success) {
    throw new ApiError(`${path} response did not match the expected schema.`);
  }
  return parsed.data;
}

/** Fetch + zod-parse a live route (mock handled by the caller). */
async function getLive<T>(path: string, schema: z.ZodType<T>): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${LANGGRAPH_URL}${path}`, {
      headers: { accept: "application/json" },
    });
  } catch {
    throw new ApiError(`Could not reach the backend at ${LANGGRAPH_URL}${path}.`);
  }
  if (!res.ok) throw new ApiError(`${path} returned ${res.status}.`, res.status);
  return parse(path, schema, await res.json());
}

async function get<T>(path: string, schema: z.ZodType<T>, mock: T): Promise<T> {
  if (USE_MOCKS) return mock;
  return getLive(path, schema);
}

/** Empty on purpose: mock mode has no engine pauses to list. */
const MOCK_PENDING: PendingClarifications = {
  rows: [],
  meta: {
    n: 0,
    truncated: false,
    threads_scanned: 0,
    limit: 50,
    offset: 0,
    columns: [
      "asked_at",
      "question",
      "why",
      "clarification_id",
      "turn_id",
      "thread_id",
      "source",
      "basis",
    ],
  },
};

export const api = {
  capabilities: (): Promise<Capabilities> =>
    get("/capabilities", capabilitiesSchema, MOCK_CAPABILITIES),
  pendingClarifications: (): Promise<PendingClarifications> =>
    get("/clarifications/pending", pendingClarificationsSchema, MOCK_PENDING),
};
