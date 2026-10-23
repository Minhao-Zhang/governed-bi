"use client";

import { Conversation } from "@/components/chat/conversation";
import { useStreamChat } from "@/hooks/use-stream-chat";
import { useThreadId } from "@/hooks/use-thread-id";

/**
 * The live transport. The open conversation is the `thread` query on the URL,
 * written back when the first turn mints one, so a reload reopens it.
 */
export function StreamChat() {
  const { threadId, setThreadId } = useThreadId();
  const { messages, send, isRunning, steps, stop, clarification, respondClarification } =
    useStreamChat(threadId, setThreadId);

  return (
    <Conversation
      messages={messages}
      send={send}
      isRunning={isRunning}
      steps={steps}
      stop={stop}
      clarification={clarification}
      respondClarification={respondClarification}
    />
  );
}
