/**
 * Conversations — the LangGraph Server's threads, listed cheaply.
 *
 * **The engine has always persisted these; the client threw them away.** `langgraph dev`
 * checkpoints every thread to `.langgraph_api/*.pckl` and they survive a restart, so a
 * conversation was recoverable the whole time. What was missing was on this side: `useStream`
 * was called with no `threadId`, so it minted a fresh one on every page load and there was no
 * way to name, list or reopen the ones already on disk.
 *
 * **`select` + `extract`, not a plain search, and the difference is three orders of magnitude.**
 * Measured against a local server holding 16 threads: `threads.search({limit: 50})` returns
 * **2,421,414 bytes**, because a thread's `values` is the whole serve state — every message, the
 * delivered context block, the retrieved assets, the record. The same query restricted to four
 * scalar fields plus two extracted paths returns **3,926 bytes**. A sidebar that re-fetches on
 * focus cannot be built on the first shape.
 *
 * `extract` takes a dotted path into the thread and must start with one of `config`,
 * `interrupts`, `metadata` or `values` — the server rejects a bare key and rejects JSONPath
 * (`$.question`) with a message naming the four roots.
 */

import { Client, type Thread } from "@langchain/langgraph-sdk";

import { API_KEY, LANGGRAPH_URL } from "@/lib/env";

/** One row of the conversation list. */
export interface ConversationSummary {
  thread_id: string;
  updated_at: string;
  status: Thread["status"];
  /**
   * The **most recent** question on the thread, not the first.
   *
   * `question` is a single state channel that each turn overwrites, so there is no "first
   * question" to read without pulling the message list — which is the 2.4 MB payload this
   * module exists to avoid. Rendered as "the last thing you asked" beside the turn count
   * rather than as a title, because a title that silently changes is worse than no title.
   */
  question: string | null;
  /** `turn_index`, i.e. how many turns this conversation has. */
  turns: number | null;
}

let client: Client | null = null;

function threadsClient(): Client {
  // One client, built lazily. `LANGGRAPH_URL` is empty in mock mode and `listConversations`
  // refuses before reaching here, so a mock-mode build never constructs one.
  // `apiKey` is the SDK's own option and sends `x-api-key`, which is the spelling
  // the engine picked so one option covers every SDK call site. Undefined rather
  // than "" when unconfigured: the SDK omits the header entirely instead of
  // presenting an empty credential the engine would reject as wrong-rather-than-absent.
  client ??= new Client({ apiUrl: LANGGRAPH_URL, apiKey: API_KEY || undefined });
  return client;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * The most recently updated conversations, newest first.
 *
 * `limit` is a real cap and not pagination: this is one operator's own traffic and the list is
 * a switcher, not an archive. If it ever needs to be one, `offset` is already on the query.
 */
export async function listConversations(limit = 50): Promise<ConversationSummary[]> {
  if (!LANGGRAPH_URL) return [];
  const threads = await threadsClient().threads.search({
    limit,
    sortBy: "updated_at",
    sortOrder: "desc",
    // No `values`. See the module note: including it is the 2.4 MB response.
    select: ["thread_id", "updated_at", "status"],
    extract: { question: "values.question", turns: "values.turn_index" },
  });
  return threads.map((thread) => ({
    thread_id: thread.thread_id,
    updated_at: thread.updated_at,
    status: thread.status,
    question: text(thread.extracted?.question),
    turns: count(thread.extracted?.turns),
  }));
}

/** Forget a conversation, on the server. Irreversible — the checkpoints go with it. */
export async function deleteConversation(threadId: string): Promise<void> {
  if (!LANGGRAPH_URL) return;
  await threadsClient().threads.delete(threadId);
}
