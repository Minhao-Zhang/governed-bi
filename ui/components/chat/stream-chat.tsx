"use client";

import { Conversation } from "@/components/chat/conversation";
import { ConversationBar } from "@/components/chat/conversation-bar";
import { RecentConversations } from "@/components/chat/recent-conversations";
import { useStreamChat } from "@/hooks/use-stream-chat";
import { useThreadId } from "@/hooks/use-thread-id";

/**
 * The live transport. It owns which conversation is open, because threads are LangGraph
 * Server's resource: held in the URL (`useThreadId`) and written back when the first turn mints
 * one, so a reload reopens the same conversation.
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
      empty={<RecentConversations onOpen={setThreadId} />}
    />
  );
}
