"use client";

import { MessageSquareText } from "lucide-react";

import { ClarificationPrompt } from "@/components/chat/clarification-prompt";
import { Composer } from "@/components/chat/composer";
import { MessageList } from "@/components/chat/message-list";
import type { ChatTransport } from "@/hooks/use-chat";

/**
 * The transport-neutral chat view: the transcript scrolls and the composer is pinned below.
 * `banner` sits above the composer, `header` above the transcript, and `empty` under the
 * new-conversation prompt; only the streaming transport has conversations to put in the last two.
 */
export function Conversation({
  messages,
  send,
  isRunning,
  steps,
  stop,
  clarification,
  respondClarification,
  banner,
  header,
  empty,
}: ChatTransport & {
  banner?: React.ReactNode;
  header?: React.ReactNode;
  empty?: React.ReactNode;
}) {
  const isEmpty = messages.length === 0 && !isRunning;
  // While the agent is waiting on a clarification the turn is paused; the user
  // answers the question, not sends a new turn, so lock the composer.
  const pendingClarification = clarification != null && respondClarification != null;

  return (
    <div className="flex h-full flex-col">
      {/* Outside the scroll container, so the switcher stays reachable in a long transcript. */}
      {header && (
        <div className="mb-3 flex shrink-0 justify-center">
          <div className="w-full max-w-5xl">{header}</div>
        </div>
      )}

      {/* Scrollable transcript. */}
      <div className="flex-1 overflow-y-auto">
        {isEmpty ? (
          <div className="flex h-full flex-col items-center justify-center gap-6 p-6">
            <div className="flex flex-col items-center gap-4 text-center">
              <MessageSquareText className="size-6 text-muted-foreground" aria-hidden />
              <p className="text-sm text-muted-foreground">Ask a question about your data</p>
            </div>
            {empty}
          </div>
        ) : (
          <div className="mx-auto w-full max-w-5xl">
            <MessageList
              messages={messages}
              isRunning={isRunning}
              steps={steps}
              awaitingClarification={pendingClarification}
            />
          </div>
        )}
      </div>

      {/* Composer pinned to the bottom, with any pending clarification above it. */}
      <div className="border-t pt-4">
        <div className="mx-auto w-full max-w-5xl">
          {pendingClarification && (
            <ClarificationPrompt request={clarification} onRespond={respondClarification} />
          )}
          {banner}
          {/* A pending clarification locks the composer but withholds Stop: Decline fails the
              turn closed and records it, whereas aborting mid-interrupt records nothing. */}
          <Composer
            onSend={send}
            isRunning={isRunning || pendingClarification}
            onStop={pendingClarification ? undefined : stop}
          />
        </div>
      </div>
    </div>
  );
}
