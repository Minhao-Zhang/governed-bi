"use client";

import { Conversation } from "@/components/chat/conversation";
import { ConversationBar } from "@/components/chat/conversation-bar";
import { useStreamChat } from "@/hooks/use-stream-chat";
import { useThreadId } from "@/hooks/use-thread-id";

/**
 * Live streaming transport container. Owns the `useStream`-backed hook; mounted
 * only when the backend reports `can_stream: true`.
 *
 * **It also owns which conversation is open**, because that is a fact about this transport and
 * no other: threads are LangGraph Server's resource, and the REST fallback has none. Held in the
 * URL (`useThreadId`), fed to the hook as `threadId`, and written back by `onThreadId` when the
 * first turn mints one — so a reload reopens the same conversation instead of starting a fresh
 * thread beside the one already on disk.
 *
 * The *list* of conversations is not here. It is `/history`, and `?thread=<id>` is the one
 * address both pages agree on.
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
      header={<ConversationBar threadId={threadId} onNew={() => setThreadId(null)} />}
      // Cancelling a clarification has to leave the thread, not just close the prompt: the graph
      // is paused at an `interrupt()` and will refuse a new turn until it is answered. Same call
      // the New button makes, for the same reason.
      onAbandonThread={() => setThreadId(null)}
    />
  );
}
