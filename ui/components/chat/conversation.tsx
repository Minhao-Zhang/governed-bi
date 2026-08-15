"use client";

import { useState } from "react";
import { MessageSquareText } from "lucide-react";

import { ClarificationPrompt } from "@/components/chat/clarification-prompt";
import { Composer } from "@/components/chat/composer";
import { MessageList } from "@/components/chat/message-list";
import { useAssets } from "@/hooks/queries";
import type { ChatTransport } from "@/hooks/use-chat";
import { api } from "@/lib/api-client";

/**
 * The chat cockpit's shared view: a full-height column where the transcript
 * scrolls and the composer is pinned to the bottom. Transport-neutral — it takes
 * the same `{ messages, send, isRunning, steps }` shape every chat hook
 * exposes, so the mock, streaming, and REST containers all render through this
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
  //: Questions this user has abandoned. Held here rather than in the transport because cancelling
  //: is not a transport event: the graph thread stays paused and is simply never resumed (the LRU
  //: evicts it), so there is nothing for `useStream` to be told. Keeping it local also means the
  //: `ChatTransport` interface — which upstream owns — does not move.
  const [cancelled, setCancelled] = useState<ReadonlySet<string>>(() => new Set());

  // While the agent is waiting on a clarification the turn is paused; the user answers the
  // question, not sends a new turn, so lock the composer. A cancelled question stops counting as
  // pending, which is what unlocks it again.
  const pendingClarification =
    clarification != null &&
    respondClarification != null &&
    !cancelled.has(clarification.clarification_id);

  async function cancelPending(id: string) {
    // Optimistic, and deliberately so: the button's job is to give the composer back, and the
    // ledger write is bookkeeping the user is not waiting on. A failed write leaves a row `open`,
    // which is the same state it was already in.
    setCancelled((prev) => new Set(prev).add(id));
    // **And stop the run.** Dropping the prompt is not enough: the composer is locked on
    // `isRunning || pendingClarification`, and `useStream` keeps reporting the run as in flight
    // while the graph sits at the interrupt — so without this the question disappears and the
    // input stays dead, which is a worse trap than the one this button exists to fix. Found by
    // clicking it. Stopping is also the honest reading: the turn *is* abandoned.
    stop?.();
    try {
      await api.cancelClarification(id);
    } catch (err) {
      // Not a toast: from here the question is gone from the user's screen either way, and an
      // error about a ledger they cannot see is noise. The admin queue is where it shows up.
      console.warn(`could not record the cancellation of ${id}`, err);
    }
  }

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
            <ClarificationPrompt
              request={clarification}
              onRespond={respondClarification}
              onCancel={() => void cancelPending(clarification.clarification_id)}
            />
          )}
          {banner}
          {/* A pending clarification locks the composer the same way a running turn does — the
              user answers the question rather than starting a new turn — and withholds Stop,
              because aborting mid-interrupt abandons the turn with nothing recorded.
              *
              This used to say Decline was the governed way out. It is not offered: this fork
              replaced it with Defer and hides Defer for `ranking_ambiguity`, which left that
              basis with no exit at all. The prompt's own Cancel button is the exit now, and it
              records what happened. */}
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
