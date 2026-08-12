/**
 * Client-visible configuration.
 *
 * Only `NEXT_PUBLIC_*` variables are inlined into the browser bundle (Next.js
 * replaces unprefixed vars with an empty string on the client). The UI is a pure
 * client of the LangGraph Server (ADR 0001): chat via `useStream`, the custom
 * REST routes via `fetch`, both against the same base URL.
 *
 * When no base URL is configured we run entirely on mock fixtures, so the whole
 * UI renders before the engine's LangGraph rework lands.
 */

export const LANGGRAPH_URL = (process.env.NEXT_PUBLIC_LANGGRAPH_URL ?? "").trim();

/** The graph name in `langgraph.json` that serves chat (e.g. `serve`). */
export const ASSISTANT_ID = (process.env.NEXT_PUBLIC_ASSISTANT_ID ?? "serve").trim();

/**
 * The engine's shared key. Every served route but `GET /livez` requires it, and a
 * server started with its own `GOVERNED_BI_API_KEY` unset refuses *everything* with
 * 401 — unset never means open. Without this the client cannot get past
 * `/capabilities`, which is what happened until 2026-08-11: the engine grew auth and
 * this repository never learned.
 *
 * The value must equal the engine's `GOVERNED_BI_API_KEY`. Two transports carry it:
 * the SDK's own `apiKey` option, which sends `x-api-key` (the engine chose that header
 * spelling precisely so `useStream` carries it for free — see `api/auth.py`), and the
 * raw `fetch` calls in `api-client.ts`, which must set the header themselves.
 *
 * `NEXT_PUBLIC_` means this is **inlined into the browser bundle and readable by
 * anyone who loads the page**. That is acceptable only because the engine's key is a
 * single shared principal for a single-operator deployment, which is the boundary
 * `governed-bi`'s own docs draw. It is not a per-user credential and must not become
 * one without a real token exchange.
 */
export const API_KEY = (process.env.NEXT_PUBLIC_GOVERNED_BI_API_KEY ?? "").trim();

/**
 * Headers a raw `fetch` against the engine needs. Empty when no key is configured, so
 * a keyless local server still works and the 401 (if any) comes from the engine rather
 * than from a header this client invented.
 */
export function authHeaders(): Record<string, string> {
  return API_KEY ? { "x-api-key": API_KEY } : {};
}

/**
 * No backend configured → drive everything from `lib/mock/fixtures`. This is the
 * default until `NEXT_PUBLIC_LANGGRAPH_URL` points at a running server.
 */
export const USE_MOCKS = LANGGRAPH_URL === "";
