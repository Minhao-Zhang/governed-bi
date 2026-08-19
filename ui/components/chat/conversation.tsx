"use client";

import { MessageSquareText } from "lucide-react";

import { ClarificationPrompt } from "@/components/chat/clarification-prompt";
import { Composer } from "@/components/chat/composer";
import { MessageList } from "@/components/chat/message-list";
import { useAssets } from "@/hooks/queries";
import type { ChatTransport } from "@/hooks/use-chat";

/**
 * The chat cockpit's shared view: a full-height column where the transcript
 * scrolls and the composer is pinned to the bottom. Transport-neutral — it takes
 * the same `{ messages, send, isRunning, steps }` shape every chat hook
 * exposes, so the mock and streaming containers both render through this
 * one component. An optional `banner` renders just above the composer (used for
 * the mock-mode preview notice), and an optional `header` sits above the transcript
 * (used by the streaming transport for the conversation switcher — the only transport
 * that has conversations to switch between).
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
}: ChatTransport & { banner?: React.ReactNode; header?: React.ReactNode }) {
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
          <div className="flex h-full items-center justify-center p-6">
            <EmptyState onPick={send} />
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
          {/* A pending clarification locks the composer the same way a running
              turn does — the user answers the question rather than starting a new
              turn. But it withholds Stop: Decline is the governed way out (it
              fails the turn closed, stamped `clarification_declined`), whereas
              aborting mid-interrupt abandons it with nothing recorded. */}
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

/**
 * The cold-start view. Beyond the prompt, it surfaces the corpus's own few-shot
 * questions as clickable starters so a first-time user learns what this governed
 * dataset can actually answer instead of facing a blank box.
 */
function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  const { data: assets } = useAssets();
  const starters = (assets ?? [])
    .filter((a) => a.asset_type === "few_shot" && a.summary.trim() !== "")
    .map((a) => a.summary)
    .slice(0, 4);

  return (
    <div className="flex max-w-md flex-col items-center gap-4 text-center">
      <MessageSquareText className="size-6 text-muted-foreground" aria-hidden />
      <p className="text-sm text-muted-foreground">Ask a question about the governed data</p>
      {starters.length > 0 && (
        <div className="flex flex-col items-center gap-2">
          <p className="text-xs text-muted-foreground/80">Try one of these</p>
          <div className="flex flex-wrap justify-center gap-2">
            {starters.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => onPick(q)}
                className="rounded-full border px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:border-ring hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
