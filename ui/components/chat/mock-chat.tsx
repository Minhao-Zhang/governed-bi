"use client";

import { Conversation } from "@/components/chat/conversation";
import { useChat } from "@/hooks/use-chat";

/**
 * Mock transport container. Owns the synthetic `useChat` state machine and keeps
 * the "Preview mode" banner pinned above the composer so it can't disappear while
 * answers are still synthetic.
 */
export function MockChat() {
  const { messages, send, isRunning, steps, stop, clarification, respondClarification } =
    useChat();

  return (
    <Conversation
      messages={messages}
      send={send}
      isRunning={isRunning}
      steps={steps}
      stop={stop}
      clarification={clarification}
      respondClarification={respondClarification}
      banner={
        <p className="mb-2 text-xs text-muted-foreground">
          Preview mode — showing synthetic output; attach a backend for live answers.
        </p>
      }
    />
  );
}
