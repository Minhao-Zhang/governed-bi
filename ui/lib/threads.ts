/**
 * Conversations: the LangGraph Server's threads, listed cheaply.
 *
 * `select` + `extract` rather than a plain search: a thread's `values` is the whole serve state,
 * so 16 threads measured 2,421,414 bytes with it and 3,926 bytes without. An `extract` path must
 * start with `config`, `interrupts`, `metadata` or `values`; the server rejects anything else.
 */

import { Client, type Thread } from "@langchain/langgraph-sdk";

import { LANGGRAPH_URL } from "@/lib/env";

/** One row of the conversation list. */
export interface ConversationSummary {
  thread_id: string;
  updated_at: string;
  status: Thread["status"];
  /**
   * The most recent question, not the first: `question` is one channel each turn overwrites, and
   * reading the first would mean pulling the message list this module exists to avoid.
   */
  question: string | null;
  /** `turn_index`, i.e. how many turns this conversation has. */
  turns: number | null;
}

let client: Client | null = null;

function threadsClient(): Client {
  // One client, built lazily. `LANGGRAPH_URL` is empty in mock mode and `listConversations`
  // refuses before reaching here, so a mock-mode build never constructs one.
  // No `apiKey`: the engine reads no credential off the wire, so there is none to send.
  client ??= new Client({ apiUrl: LANGGRAPH_URL });
  return client;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The most recently updated conversations, newest first. `limit` is a cap, not pagination. */
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
