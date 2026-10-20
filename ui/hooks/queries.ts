/**
 * React Query hooks for the chat page. Param-less keys stay stable so the app shares one cache.
 */

"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api-client";
import { USE_MOCKS } from "@/lib/env";
import { listConversations } from "@/lib/threads";

export function useCapabilities() {
  return useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities });
}

/**
 * The server's persisted threads, newest first, through the SDK client (`lib/threads.ts`).
 * `refetchOnWindowFocus` stays on so a conversation started in another tab shows on return.
 */
export function useConversations(limit = 50) {
  return useQuery({
    queryKey: ["conversations", limit] as const,
    queryFn: () => listConversations(limit),
    enabled: !USE_MOCKS,
  });
}
