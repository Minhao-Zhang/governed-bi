"use client";

import { useEffect, useRef } from "react";

import { Answer } from "@/components/answer/answer";
import { StatusLine } from "@/components/chat/status-line";
import type { ChatMessage } from "@/hooks/use-chat";
import type { TimelineStep } from "@/lib/steps";

/**
 * The transcript. A question is a grey bubble on the right; an answer is plain
 * text on the left. While a turn is running, one status line sits under the question.
 */
export function MessageList({
  messages,
  isRunning,
  steps,
}: {
  messages: ChatMessage[];
  isRunning: boolean;
  steps?: TimelineStep[];
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Instant while a turn is in flight, smooth once it settles, and only when the
  // reader is already at the bottom. A smooth scroll retargets on every stage.
  useEffect(() => {
    const anchor = bottomRef.current;
    if (!anchor || !isPinnedToBottom(anchor)) return;
    anchor.scrollIntoView({ behavior: isRunning ? "instant" : "smooth", block: "end" });
  }, [messages.length, isRunning, steps?.length]);

  return (
    <div className="space-y-6 py-4">
      {messages.map((message) =>
        message.role === "user" ? (
          <QuestionBubble key={message.id} text={message.text ?? ""} />
        ) : (
          <div key={message.id}>
            {message.answer ? (
              <Answer answer={message.answer} />
            ) : (
              message.text && <p className="text-sm text-[var(--text)]">{message.text}</p>
            )}
          </div>
        ),
      )}

      {isRunning && <StatusLine steps={steps} />}

      <div ref={bottomRef} />
    </div>
  );
}

/** How far from the bottom still counts as following along, in px. */
const PIN_SLACK = 160;

/** The column's scroller is an ancestor. A box that does not overflow counts as pinned. */
function isPinnedToBottom(anchor: HTMLElement): boolean {
  for (let el = anchor.parentElement; el != null; el = el.parentElement) {
    const overflowY = getComputedStyle(el).overflowY;
    if (overflowY !== "auto" && overflowY !== "scroll") continue;
    if (el.scrollHeight <= el.clientHeight) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight <= PIN_SLACK;
  }
  return true;
}

function QuestionBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div
        data-question
        className="max-w-[80%] whitespace-pre-wrap rounded-2xl bg-[var(--bubble)] px-4 py-2 text-sm text-[var(--text)]"
      >
        {text}
      </div>
    </div>
  );
}
