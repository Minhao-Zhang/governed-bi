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
  onAbandonThread,
}: ChatTransport & {
  banner?: React.ReactNode;
  header?: React.ReactNode;
  /** Leave the conversation this turn is stuck in. See `cancelPending`: a LangGraph thread paused
   * at an `interrupt()` cannot accept a new turn, so cancelling has to give up the thread, not
   * just the prompt. Only the streaming transport has threads, so this is optional. */
  onAbandonThread?: () => void;
}) {
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

  /**
   * Abandon a pending question. Three steps, and the third one is the one that actually works.
   *
   * The first cut did only the first two and **silently ate the next question the user typed**.
   * `use-stream-chat.ts`'s `send` refuses while `awaitingClarification`, which it derives from
   * `stream.interrupt` — the *thread's* pending interrupt, which no amount of client-side state
   * clears. `stop()` aborts the HTTP stream, not the interrupt. So the Composer cleared its input,
   * `send` returned early, and nothing left the browser: no error, no console line, no row.
   *
   * That guard is right, and its own comment says why — a thread waiting on an answer to its first
   * turn cannot start a second. Which means cancelling has to **give up the thread**. It is also
   * the honest reading: the turn is abandoned, and so is the conversation it was stuck in. The
   * transcript is still readable under History and Audit, so nothing is lost but the on-screen
   * scrollback.
   *
   * `setCancelled` stays anyway, and not as belt-and-braces: the mock and REST transports render
   * this same component and have no threads, so it is the only thing that closes the prompt for
   * them. Two mechanisms because there are two situations, not two attempts at one.
   */
  async function cancelPending(id: string) {
    setCancelled((prev) => new Set(prev).add(id));
    stop?.();
    onAbandonThread?.();
    try {
      await api.cancelClarification(id);
    } catch (err) {
      // Not a toast: the question is gone from the user's screen either way, and an error about a
      // ledger they cannot see is noise. The admin queue is where a missed write shows up.
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
