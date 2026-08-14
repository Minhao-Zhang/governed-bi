"use client";

/**
 * useRestChat — the non-streaming fallback transport.
 *
 * Used when a backend is attached but `/capabilities` reports `can_stream:
 * false` (no LangGraph Server streaming). Each turn is a one-shot `POST /chat`
 * (see `api.chat`), which returns only a final answer — no live governance
 * stream. So there is no timeline here; `isRunning` alone drives a plain
 * indeterminate spinner (rendered by <ServeProgress/>) while the request is in
 * flight.
 *
 * Errors surface as a toast and stop the run (the transcript keeps the user's
 * turn but gains no assistant answer).
 */

import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";

import type { ChatMessage, ChatTransport } from "@/hooks/use-chat";
import { ApiError, api } from "@/lib/api-client";
import type { ChatTurn } from "@/lib/types";

// Module-level fallback counter so ids stay unique without an external dep.
let idCounter = 0;

function nextId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  idCounter += 1;
  return `rest-${idCounter}`;
}

export function useRestChat(): ChatTransport {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isRunning, setIsRunning] = useState(false);

  // One stable session id for the whole conversation (created once, client-side).
  const sessionIdRef = useRef<string | null>(null);
  if (sessionIdRef.current === null) sessionIdRef.current = nextId();

  const send = useCallback(
    (question: string) => {
      const trimmed = question.trim();
      if (!trimmed || isRunning) return;

      // Build the prior-turn history from the transcript BEFORE this turn.
      const history: ChatTurn[] = messages
        .map((message) => ({
          role: message.role,
          text: message.text ?? message.answer?.text ?? "",
        }))
        .filter((turn) => turn.text !== "");

      // 1) Push the user's turn and enter the running state. `isRunning` shows a
      // plain indeterminate spinner — POST /chat has no live progress to report.
      setMessages((prev) => [...prev, { id: nextId(), role: "user", text: trimmed }]);
      setIsRunning(true);

      // 2) Fire the request; append the assistant answer or toast on failure.
      const sessionId = sessionIdRef.current ?? nextId();
      api
        .chat(trimmed, history, sessionId)
        .then((answer) => {
          setMessages((prev) => [...prev, { id: nextId(), role: "assistant", answer }]);
        })
        .catch((error: unknown) => {
          const message =
            error instanceof ApiError
              ? error.message
              : "The backend could not answer that question.";
          toast.error(message);
        })
        .finally(() => {
          setIsRunning(false);
        });
    },
    [isRunning, messages],
  );

  return { messages, send, isRunning };
}
