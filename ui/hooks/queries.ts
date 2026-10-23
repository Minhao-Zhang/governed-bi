/**
 * React Query hooks for the chat page. Param-less keys stay stable so the app shares one cache.
 */

"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api-client";
import { USE_MOCKS } from "@/lib/env";
import { listConversations } from "@/lib/threads";

/** The sidebar is not on the chat stream, so both lists poll. */
const SIDEBAR_POLL_MS = 5_000;

export function useCapabilities() {
  return useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities });
}

/** Threads, newest first. Polled, and refreshed on focus. */
export function useConversations(limit = 50) {
  return useQuery({
    queryKey: ["conversations", limit] as const,
    queryFn: () => listConversations(limit),
    enabled: !USE_MOCKS,
    refetchInterval: SIDEBAR_POLL_MS,
    refetchOnWindowFocus: true,
  });
}

/** Unanswered clarifying questions (`GET /clarifications/pending`), oldest first. */
export function usePendingClarifications() {
  return useQuery({
    queryKey: ["clarifications", "pending"] as const,
    queryFn: api.pendingClarifications,
    enabled: !USE_MOCKS,
    refetchInterval: SIDEBAR_POLL_MS,
    refetchOnWindowFocus: true,
  });
}
