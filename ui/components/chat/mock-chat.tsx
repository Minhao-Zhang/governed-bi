"use client";

import { Conversation } from "@/components/chat/conversation";
import { useChat } from "@/hooks/use-chat";

/** Mock transport. The synthetic turns come from `useChat` and the fixtures. */
export function MockChat() {
  const { messages, send, isRunning, steps, stop, clarification, respondClarification } =
    useChat();

  return (
    <div className="flex h-full flex-col">
      <p className="shrink-0 px-4 pt-3 text-center text-xs text-[var(--muted)]">
        Preview mode — showing synthetic output; attach a backend for live answers.
      </p>
      <div className="min-h-0 flex-1">
        <Conversation
          messages={messages}
          send={send}
          isRunning={isRunning}
          steps={steps}
          stop={stop}
          clarification={clarification}
          respondClarification={respondClarification}
        />
      </div>
    </div>
  );
}
