"use client";

import { ClarificationPrompt } from "@/components/chat/clarification-prompt";
import { Composer } from "@/components/chat/composer";
import { ComposerDraftProvider } from "@/components/chat/composer-draft";
import { EmptyState, ExampleQuestions } from "@/components/chat/empty-state";
import { MessageList } from "@/components/chat/message-list";
import type { ChatTransport } from "@/hooks/use-chat";

/** The centred column: the transcript scrolls and the composer stays at the bottom. */
export function Conversation({
  messages,
  send,
  isRunning,
  steps,
  stop,
  clarification,
  respondClarification,
}: ChatTransport) {
  const isEmpty = messages.length === 0 && !isRunning;
  // A clarification answers the suspended turn, so the composer stays locked and
  // Stop is withheld: declining closes the turn, aborting records nothing.
  const pendingClarification = clarification != null && respondClarification != null;

  return (
    <ComposerDraftProvider>
      <div className="flex h-full justify-center text-[var(--text)]">
        <div className="flex h-full w-full max-w-[720px] flex-col px-4">
          <div className="flex-1 overflow-y-auto">
            {isEmpty ? (
              <EmptyState />
            ) : (
              <MessageList messages={messages} isRunning={isRunning} steps={steps} send={send} />
            )}
          </div>

          <div className="shrink-0 pb-4 pt-2">
            {pendingClarification && (
              <ClarificationPrompt request={clarification} onRespond={respondClarification} />
            )}
            <Composer
              onSend={send}
              isRunning={isRunning || pendingClarification}
              onStop={pendingClarification ? undefined : stop}
            />
            {isEmpty && <ExampleQuestions />}
          </div>
        </div>
      </div>
    </ComposerDraftProvider>
  );
}
