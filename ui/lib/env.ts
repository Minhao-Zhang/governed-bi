/**
 * Client-visible configuration.
 *
 * Only `NEXT_PUBLIC_*` variables are inlined into the browser bundle (Next.js
 * replaces unprefixed vars with an empty string on the client). The UI is a pure
 * client of the LangGraph Server (ADR 0001): chat via `useStream`, the custom
 * REST routes via `fetch`, both against the same base URL.
 *
 * When no base URL is configured we run entirely on mock fixtures, so every surface renders
 * against no engine at all. That began as scaffolding — the engine's LangGraph runtime has
 * since landed (ADR 0001; `langgraph.json` declares the `serve` graph) — and it is kept now
 * for layout work, which is also the trap. See `USE_MOCKS` below.
 */

export const LANGGRAPH_URL = (process.env.NEXT_PUBLIC_LANGGRAPH_URL ?? "").trim();

/** The graph name in `langgraph.json` that serves chat (e.g. `serve`). */
export const ASSISTANT_ID = (process.env.NEXT_PUBLIC_ASSISTANT_ID ?? "serve").trim();

// There is no credential here: the engine dropped transport auth, so no route reads
// `x-api-key` or `Authorization` and this client has nothing to carry.

/**
 * No backend configured — drive everything from `lib/mock/fixtures`. This is the default
 * until `NEXT_PUBLIC_LANGGRAPH_URL` points at a running server.
 *
 * **A build-time inline, so this is a deploy-time hazard and not only a dev convenience.**
 * Next.js replaces `process.env.NEXT_PUBLIC_*` at build time, so a production bundle built
 * without the variable ships the synthetic transport and every fixture with it: a complete,
 * plausible UI answering questions no engine ever saw. `<MockChat/>` pins a banner saying so
 * (`components/chat/mock-chat.tsx`) and `.env.example` says it too. Do not remove either.
 */
export const USE_MOCKS = LANGGRAPH_URL === "";
