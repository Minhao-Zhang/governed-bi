"use client";

import { Conversation } from "@/components/chat/conversation";
import { useRestChat } from "@/hooks/use-rest-chat";

/**
 * Non-streaming transport container. Owns the `POST /chat`-backed hook; mounted
 * when a backend is attached but reports `can_stream: false`.
 */
export function RestChat() {
  const { messages, send, isRunning } = useRestChat();

  return <Conversation messages={messages} send={send} isRunning={isRunning} />;
}
