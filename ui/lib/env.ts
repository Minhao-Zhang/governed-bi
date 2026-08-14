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

// There is no credential here: the engine dropped transport auth, so no route reads
// `x-api-key` or `Authorization` and this client has nothing to carry.

/**
 * No backend configured → drive everything from `lib/mock/fixtures`. This is the
 * default until `NEXT_PUBLIC_LANGGRAPH_URL` points at a running server.
 */
export const USE_MOCKS = LANGGRAPH_URL === "";
