/**
 * React Query hooks for the chat page. Param-less keys stay stable so the app shares one cache.
 */

"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api-client";
import { USE_MOCKS } from "@/lib/env";
import type { Ending } from "@/lib/types";

/** The conversation list is not on the chat stream, so it polls. */
const LIST_POLL_MS = 5_000;

export function useCapabilities() {
  return useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities });
}

/** `GET /conversations`, newest first. Polled, and refreshed on focus. */
export function useConversations({
  limit = 50,
  ending = null,
}: { limit?: number; ending?: Ending | null } = {}) {
  return useQuery({
    queryKey: ["conversations", limit, ending] as const,
    queryFn: () => api.conversations({ limit, ending }),
    enabled: !USE_MOCKS,
    refetchInterval: LIST_POLL_MS,
    refetchOnWindowFocus: true,
  });
}

/** Few-shot questions (`GET /examples`). A failure leaves the caller nothing to show. */
export function useExamples(params: { limit: number; near?: string }) {
  return useQuery({
    queryKey: ["examples", params.limit, params.near ?? ""] as const,
    queryFn: () => api.examples(params),
    retry: false,
  });
}
