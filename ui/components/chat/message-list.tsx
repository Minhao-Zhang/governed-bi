"use client";

import { useEffect, useRef } from "react";

import { AnswerCard } from "@/components/answer/answer-card";
import { ServeProgress } from "@/components/chat/serve-progress";
import type { ChatMessage } from "@/hooks/use-chat";
import type { TimelineStep } from "@/lib/steps";

/**
 * The transcript. User turns are right-aligned bubbles; assistant turns render
 * a full <AnswerCard/>. While the agent is running (before its answer lands), a
 * placeholder assistant bubble shows the running progress — the live agent
 * timeline, or a plain spinner before the first event (and in `business` mode, which is not
 * shown the trace). There is no REST fallback to spin for — `POST /chat` is deleted.
 * Auto-scrolls to the newest turn as messages arrive or progress advances.
 */
export function MessageList({
  messages,
  isRunning,
  steps,
  awaitingClarification = false,
}: {
  messages: ChatMessage[];
  isRunning: boolean;
  steps?: TimelineStep[];
  /** A clarification is pending: the turn is suspended, not finished, so keep
   * the running-progress view mounted beside the prompt. */
  awaitingClarification?: boolean;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  // The pipeline is either actively running or paused waiting on the user; both
  // keep the live agent timeline on screen.
  const inProgress = isRunning || awaitingClarification;

  // Keep the latest turn in view on new messages or new timeline rows (the agent loop grows as
  // events arrive).
  //
  // Two conditions on that, both learned from watching a live turn. A *smooth* scroll is an
  // animation, and a governed turn emits a timeline row every few hundred milliseconds: each one
  // retargeted the animation before it landed, so the transcript slid continuously instead of
  // following the log. And an unconditional scroll makes reading an earlier answer mid-run
  // impossible — every row yanked the view back down. So: instant while a turn is in flight,
  // smooth when it settles, and only when the user is already at the bottom to begin with.
  useEffect(() => {
    const anchor = bottomRef.current;
    if (!anchor || !isPinnedToBottom(anchor)) return;
    anchor.scrollIntoView({ behavior: inProgress ? "instant" : "smooth", block: "end" });
  }, [messages.length, inProgress, steps?.length]);

  return (
    <div className="space-y-4 py-2">
      {messages.map((message) =>
        message.role === "user" ? (
          <UserBubble key={message.id} text={message.text ?? ""} />
        ) : (
          <div key={message.id} className="w-full">
            {message.answer ? (
              <AnswerCard answer={message.answer} steps={message.steps} />
            ) : (
              // Defensive: assistant turns carry an AnswerView in practice.
              <p className="text-sm text-muted-foreground">{message.text}</p>
            )}
          </div>
        ),
      )}

      {/* Assistant placeholder: the serve pipeline running before its answer —
          also shown while suspended at a clarification, so the timeline stays. */}
      {inProgress && (
        <div className="w-full rounded-lg border bg-card p-4">
          {/* `isRunning`, not `inProgress`: the two differ exactly while suspended at a
              clarification, and that is the one state where "working" is false. */}
          <ServeProgress
            isRunning={isRunning}
            steps={steps}
            awaitingClarification={awaitingClarification}
          />
        </div>
      )}

      {/* Scroll anchor. */}
      <div ref={bottomRef} />
    </div>
  );
}

/** How far from the bottom still counts as "following along", in px. */
const PIN_SLACK = 160;

/**
 * Is the scroller holding `anchor` at (or near) its bottom?
 *
 * The scroll container is <Conversation/>'s, not this component's, and it is not handed down —
 * so it is found by walking up to the first scrollable ancestor rather than by threading a ref
 * through a transport-neutral view for the sake of one measurement. No scroller found (or a
 * container that does not overflow yet) counts as pinned: with nothing to scroll there is no
 * reading position to preserve.
 */
function isPinnedToBottom(anchor: HTMLElement): boolean {
  for (let el = anchor.parentElement; el != null; el = el.parentElement) {
    const overflowY = getComputedStyle(el).overflowY;
    if (overflowY !== "auto" && overflowY !== "scroll") continue;
    if (el.scrollHeight <= el.clientHeight) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight <= PIN_SLACK;
  }
  return true;
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] whitespace-pre-wrap rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground">
        {text}
      </div>
    </div>
  );
}
